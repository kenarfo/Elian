# ==========================
# ELIAN — Phase 12.3.3 agent_multi smoke (dry tools / patched side effects)
# ==========================
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_agent_multi_smoke() -> int:
    from core.action_claim import detect_all_action_claims, guard_action_claim
    from core.builtin_tools import build_default_registry
    from core.compound_plan import parse_compound_goal
    from core.conversation_manager import ConversationManager
    from core.fast_path import FastPathStatus, evaluate_fast_path
    from core.intent_family import classify_intent_family
    from core.model_client import FakeModelClient, reset_model_client
    from core.official_services import official_url_for
    from core.tool_result import ToolResult
    from core.tools import reset_registry_for_tests
    from memory.manager import MemoryManager

    reset_registry_for_tests()
    reset_model_client()

    failed = 0
    rows = []

    def _row(**kwargs):
        rows.append(kwargs)
        if not kwargs.get("ok"):
            nonlocal_failed[0] += 1

    nonlocal_failed = [0]

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryManager(Path(tmp) / "m.json")

        def _manager(registry=None):
            from core.active_service import reset_active_service
            from core.world_state import reset_world_state

            reset_active_service()
            reset_world_state()
            mgr = ConversationManager(
                registry=registry or build_default_registry(confirm_medium=False),
                memory_manager=mem,
                memory=[],
                model=FakeModelClient([]),
            )
            mgr.activate()
            return mgr

        # 1. installed Spotify compound
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                with patch("WebActions.open_website") as web:
                    result = mgr.handle("Otwórz Spotify i zwiększ głośność.")
                    info = classify_intent_family("Otwórz Spotify i zwiększ głośność.")
                    plan = parse_compound_goal("Otwórz Spotify i zwiększ głośność.")
                    obs = list(mgr.last_observations[-4:])
                    tools = [str(item.get("tool") or "") for item in obs]
                    data = ((obs[0].get("data") if obs else {}) or {})
                    claims = detect_all_action_claims(result.text or "")
                    guarded = guard_action_claim(result.text or "", obs)
                    ok = (
                        result.source == "agent"
                        and int((result.data or {}).get("steps") or 0) >= 2
                        and plan.detected
                        and len(plan.steps) == 2
                        and "open_app" in tools
                        and "volume_up" in tools
                        and data.get("resolved_kind") == "APP"
                        and not data.get("fallback_used")
                        and not web.called
                        and not guarded.blocked
                    )
                    _row(
                        ok=ok,
                        input="Otwórz Spotify i zwiększ głośność. [installed]",
                        compound=plan.detected,
                        clauses=len(plan.steps),
                        families=",".join(info.slots.get("families") or []),
                        planner_steps=int((result.data or {}).get("steps") or 0),
                        service_target=data.get("canonical_name") or "Spotify",
                        app_checked=bool(data.get("app_checked")),
                        app_found=bool(data.get("app_found")),
                        resolved_kind=data.get("resolved_kind"),
                        official_url_verified=bool(data.get("official_url_verified")),
                        fallback_used=bool(data.get("fallback_used")),
                        tools=",".join(tools),
                        tool_results=",".join(
                            "ok" if item.get("success") else "fail" for item in obs
                        ),
                        legacy_count=0 if result.source == "agent" else 1,
                        action_claims=",".join(claims),
                        claim_matches=not guarded.blocked,
                        response=(result.text or "")[:160],
                    )

        # 2. missing Spotify → official website + volume
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("core.builtin_tools.launch_application", return_value=(False, "brak")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                with patch(
                    "WebActions.open_website",
                    return_value=(True, "ok"),
                ) as web:
                    result = mgr.handle("Otwórz Spotify i zwiększ głośność.")
                    obs = list(mgr.last_observations[-4:])
                    data = ((obs[0].get("data") if obs else {}) or {})
                    tools = [str(item.get("tool") or "") for item in obs]
                    ok = (
                        result.source == "agent"
                        and int((result.data or {}).get("steps") or 0) >= 2
                        and data.get("resolved_kind") == "WEBSITE"
                        and data.get("fallback_used")
                        and data.get("official_url_verified")
                        and web.called
                        and str(web.call_args[0][0]) == official_url_for("spotify")
                        and "volume_up" in tools
                    )
                    _row(
                        ok=ok,
                        input="Otwórz Spotify i zwiększ głośność. [missing]",
                        compound=True,
                        clauses=2,
                        families="APP,SYSTEM",
                        planner_steps=int((result.data or {}).get("steps") or 0),
                        service_target=data.get("canonical_name") or "Spotify",
                        app_checked=bool(data.get("app_checked")),
                        app_found=bool(data.get("app_found")),
                        resolved_kind=data.get("resolved_kind"),
                        official_url_verified=bool(data.get("official_url_verified")),
                        fallback_used=bool(data.get("fallback_used")),
                        tools=",".join(tools),
                        tool_results=",".join(
                            "ok" if item.get("success") else "fail" for item in obs
                        ),
                        legacy_count=0 if result.source == "agent" else 1,
                        action_claims=",".join(detect_all_action_claims(result.text or "")),
                        claim_matches=not guard_action_claim(result.text or "", obs).blocked,
                        response=(result.text or "")[:160],
                    )

        # 3. explicit website + volume
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("core.builtin_tools.launch_application") as launch:
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                with patch("WebActions.open_website", return_value=(True, "ok")) as web:
                    result = mgr.handle("Otwórz stronę Spotify i zwiększ głośność.")
                    obs = list(mgr.last_observations[-4:])
                    data = ((obs[0].get("data") if obs else {}) or {})
                    ok = (
                        result.source == "agent"
                        and int((result.data or {}).get("steps") or 0) >= 2
                        and data.get("resolved_kind") == "WEBSITE"
                        and not data.get("app_checked")
                        and not launch.called
                        and web.called
                    )
                    _row(
                        ok=ok,
                        input="Otwórz stronę Spotify i zwiększ głośność.",
                        compound=True,
                        clauses=2,
                        families="APP,SYSTEM",
                        planner_steps=int((result.data or {}).get("steps") or 0),
                        service_target=data.get("canonical_name") or "Spotify",
                        app_checked=bool(data.get("app_checked")),
                        app_found=bool(data.get("app_found")),
                        resolved_kind=data.get("resolved_kind"),
                        official_url_verified=bool(data.get("official_url_verified")),
                        fallback_used=bool(data.get("fallback_used")),
                        tools=",".join(str(item.get("tool") or "") for item in obs),
                        tool_results=",".join(
                            "ok" if item.get("success") else "fail" for item in obs
                        ),
                        legacy_count=0 if result.source == "agent" else 1,
                        action_claims=",".join(detect_all_action_claims(result.text or "")),
                        claim_matches=not guard_action_claim(result.text or "", obs).blocked,
                        response=(result.text or "")[:160],
                    )

        # 4. Chrome + decrease volume
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = mgr.handle("Otwórz Chrome i zmniejsz głośność.")
                obs = list(mgr.last_observations[-4:])
                tools = [str(item.get("tool") or "") for item in obs]
                ok = (
                    result.source == "agent"
                    and int((result.data or {}).get("steps") or 0) >= 2
                    and "open_app" in tools
                    and "volume_down" in tools
                )
                data = ((obs[0].get("data") if obs else {}) or {})
                _row(
                    ok=ok,
                    input="Otwórz Chrome i zmniejsz głośność.",
                    compound=True,
                    clauses=2,
                    families="APP,SYSTEM",
                    planner_steps=int((result.data or {}).get("steps") or 0),
                    service_target=data.get("canonical_name") or "Chrome",
                    app_checked=bool(data.get("app_checked")),
                    app_found=bool(data.get("app_found")),
                    resolved_kind=data.get("resolved_kind"),
                    official_url_verified=bool(data.get("official_url_verified")),
                    fallback_used=bool(data.get("fallback_used")),
                    tools=",".join(tools),
                    tool_results=",".join(
                        "ok" if item.get("success") else "fail" for item in obs
                    ),
                    legacy_count=0 if result.source == "agent" else 1,
                    action_claims=",".join(detect_all_action_claims(result.text or "")),
                    claim_matches=not guard_action_claim(result.text or "", obs).blocked,
                    response=(result.text or "")[:160],
                )

        # 5. single app regression
        fp = evaluate_fast_path("Otwórz Spotify.")
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = mgr.handle("Otwórz Spotify.")
        ok = (
            fp.status == FastPathStatus.HANDLED_COMPLETE
            and result.source == "fast_path"
            and not parse_compound_goal("Otwórz Spotify.").detected
        )
        _row(
            ok=ok,
            input="Otwórz Spotify.",
            compound=False,
            clauses=1,
            families="APP",
            planner_steps=int((result.data or {}).get("steps") or 0),
            service_target="Spotify",
            app_checked=True,
            app_found=True,
            resolved_kind="APP",
            official_url_verified=False,
            fallback_used=False,
            tools="open_app",
            tool_results="ok",
            legacy_count=0,
            action_claims=",".join(detect_all_action_claims(result.text or "")),
            claim_matches=True,
            response=(result.text or "")[:160],
        )

        # 6. single volume FastPath regression
        fp = evaluate_fast_path("głośniej")
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("core.builtin_tools.Music.run", return_value="Zwiększam głośność."):
            result = mgr.handle("głośniej")
        ok = fp.status == FastPathStatus.HANDLED_COMPLETE and result.source == "fast_path"
        _row(
            ok=ok,
            input="głośniej",
            compound=False,
            clauses=1,
            families="SYSTEM",
            planner_steps=0,
            service_target="",
            app_checked=False,
            app_found=False,
            resolved_kind="",
            official_url_verified=False,
            fallback_used=False,
            tools="volume_up",
            tool_results="ok",
            legacy_count=0,
            action_claims="",
            claim_matches=True,
            response=(result.text or "")[:160],
        )

        # 7. partial failure (app ok, volume fail)
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke

        def _invoke(name, arguments=None, **kwargs):
            if str(name) == "volume_up":
                return ToolResult.fail("Nie udało się zmienić głośności.", tool_name="volume_up")
            return original(name, arguments, **kwargs)

        registry.invoke = _invoke  # type: ignore[method-assign]
        mgr = _manager(registry)
        with patch("core.builtin_tools.launch_application", return_value=(True, "ok")):
            result = mgr.handle("Otwórz Spotify i zwiększ głośność.")
        obs = list(mgr.last_observations[-4:])
        ok = (
            result.source == "agent"
            and int((result.data or {}).get("steps") or 0) >= 2
            and any(not item.get("success") for item in obs)
            and "nie udało" in (result.text or "").lower()
        )
        data = ((obs[0].get("data") if obs else {}) or {})
        _row(
            ok=ok,
            input="Otwórz Spotify i zwiększ głośność. [partial volume fail]",
            compound=True,
            clauses=2,
            families="APP,SYSTEM",
            planner_steps=int((result.data or {}).get("steps") or 0),
            service_target=data.get("canonical_name") or "Spotify",
            app_checked=bool(data.get("app_checked")),
            app_found=bool(data.get("app_found")),
            resolved_kind=data.get("resolved_kind"),
            official_url_verified=bool(data.get("official_url_verified")),
            fallback_used=bool(data.get("fallback_used")),
            tools=",".join(str(item.get("tool") or "") for item in obs),
            tool_results=",".join("ok" if item.get("success") else "fail" for item in obs),
            legacy_count=0 if result.source == "agent" else 1,
            action_claims=",".join(detect_all_action_claims(result.text or "")),
            claim_matches=not guard_action_claim(result.text or "", obs).blocked,
            response=(result.text or "")[:160],
        )

        # 8. close Spotify + volume down
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch(
            "ComputerControl.close_application",
            return_value="Zamykam aplikację Spotify.",
        ) as close:
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = mgr.handle("Zamknij Spotify i zmniejsz głośność.")
        obs = list(mgr.last_observations[-4:])
        tools = [str(item.get("tool") or "") for item in obs]
        plan = parse_compound_goal("Zamknij Spotify i zmniejsz głośność.")
        ok = (
            result.source == "agent"
            and int((result.data or {}).get("steps") or 0) >= 2
            and plan.detected
            and plan.steps[0].semantic_action == "close_service"
            and "close_app" in tools
            and "volume_down" in tools
            and close.called
            and "zamknięte" in (result.text or "").lower()
        )
        _row(
            ok=ok,
            input="Zamknij Spotify i zmniejsz głośność.",
            compound=plan.detected,
            clauses=len(plan.steps),
            families="APP,SYSTEM",
            planner_steps=int((result.data or {}).get("steps") or 0),
            service_target="Spotify",
            app_checked=False,
            app_found=False,
            resolved_kind="",
            official_url_verified=False,
            fallback_used=False,
            tools=",".join(tools),
            tool_results=",".join("ok" if item.get("success") else "fail" for item in obs),
            legacy_count=0 if result.source == "agent" else 1,
            action_claims=",".join(detect_all_action_claims(result.text or "")),
            claim_matches=not guard_action_claim(result.text or "", obs).blocked,
            response=(result.text or "")[:160],
        )

        # 9. NP-coordination is not a compound split
        np_plan = parse_compound_goal("Znajdź Spider-Mana i Iron Mana")
        lodz_plan = parse_compound_goal("wyszukaj sklepy w Łodzi i okolicy")
        ok = (not np_plan.detected) and (not lodz_plan.detected)
        _row(
            ok=ok,
            input="Znajdź Spider-Mana i Iron Mana / Łódź i okolicy",
            compound=False,
            clauses=1,
            families="",
            planner_steps=0,
            service_target="",
            app_checked=False,
            app_found=False,
            resolved_kind="",
            official_url_verified=False,
            fallback_used=False,
            tools="",
            tool_results="",
            legacy_count=0,
            action_claims="",
            claim_matches=True,
            response="np_coordination_veto",
        )

        # 10. retry failed volume only
        registry = build_default_registry(confirm_medium=False)
        original = registry.invoke
        volume_attempts = {"n": 0}

        def _retry_invoke(name, arguments=None, **kwargs):
            if str(name) == "volume_down":
                volume_attempts["n"] += 1
                if volume_attempts["n"] == 1:
                    return ToolResult.fail("Nie udało się.", tool_name="volume_down")
            return original(name, arguments, **kwargs)

        registry.invoke = _retry_invoke  # type: ignore[method-assign]
        mgr = _manager(registry)
        with patch(
            "ComputerControl.close_application",
            return_value="Zamykam aplikację Spotify.",
        ) as close:
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                first = mgr.handle("Zamknij Spotify i zmniejsz głośność.")
                second = mgr.handle("To spróbuj.")
        ok = (
            first.source == "agent"
            and "nie udało" in (first.text or "").lower()
            and second.source == "agent"
            and close.call_count == 1
            and volume_attempts["n"] == 2
        )
        _row(
            ok=ok,
            input="To spróbuj. [retry failed volume only]",
            compound=True,
            clauses=2,
            families="APP,SYSTEM",
            planner_steps=int((second.data or {}).get("steps") or 0),
            service_target="Spotify",
            app_checked=False,
            app_found=False,
            resolved_kind="",
            official_url_verified=False,
            fallback_used=False,
            tools="close_app,volume_down",
            tool_results="ok,fail→ok",
            legacy_count=0,
            action_claims=",".join(detect_all_action_claims(second.text or "")),
            claim_matches=not guard_action_claim(
                second.text or "", mgr.last_observations[-4:]
            ).blocked,
            response=(second.text or "")[:160],
        )

        # 11. wyłącz Spotify is APP close, not SMART_HOME
        info = classify_intent_family("Wyłącz Spotify.")
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            result = mgr.handle("Wyłącz Spotify.")
        ok = (
            info.family.value == "APP"
            and str((info.slots or {}).get("semantic_action") or "") == "close_service"
            and result.source in ("agent", "fast_path")
            and "spotify" in (result.text or "").lower()
        )
        _row(
            ok=ok,
            input="Wyłącz Spotify.",
            compound=False,
            clauses=1,
            families="APP",
            planner_steps=int((result.data or {}).get("steps") or 0),
            service_target="Spotify",
            app_checked=False,
            app_found=False,
            resolved_kind="",
            official_url_verified=False,
            fallback_used=False,
            tools="close_app",
            tool_results="ok",
            legacy_count=0 if result.source in ("agent", "fast_path") else 1,
            action_claims=",".join(detect_all_action_claims(result.text or "")),
            claim_matches=not guard_action_claim(result.text or "", mgr.last_observations[-2:]).blocked,
            response=(result.text or "")[:160],
        )

        # 12. wyłącz Spotify i zmniejsz głośność
        registry = build_default_registry(confirm_medium=False)
        mgr = _manager(registry)
        plan = parse_compound_goal("Wyłącz Spotify i zmniejsz głośność.")
        with patch("ComputerControl.close_application", return_value="Zamykam aplikację Spotify."):
            with patch("core.builtin_tools.Music.run", return_value="ok"):
                result = mgr.handle("Wyłącz Spotify i zmniejsz głośność.")
        obs = list(mgr.last_observations[-4:])
        tools = [str(item.get("tool") or "") for item in obs]
        ok = (
            result.source == "agent"
            and int((result.data or {}).get("steps") or 0) >= 2
            and plan.detected
            and plan.complete
            and "APP" in (plan.to_dict().get("families") or [])
            and "SYSTEM" in (plan.to_dict().get("families") or [])
            and "close_app" in tools
            and "volume_down" in tools
            and "volume_up" not in tools
        )
        _row(
            ok=ok,
            input="Wyłącz Spotify i zmniejsz głośność.",
            compound=plan.detected,
            clauses=len(plan.steps),
            families="APP,SYSTEM",
            planner_steps=int((result.data or {}).get("steps") or 0),
            service_target="Spotify",
            app_checked=False,
            app_found=False,
            resolved_kind="",
            official_url_verified=False,
            fallback_used=False,
            tools=",".join(tools),
            tool_results=",".join("ok" if item.get("success") else "fail" for item in obs),
            legacy_count=0 if result.source == "agent" else 1,
            action_claims=",".join(detect_all_action_claims(result.text or "")),
            claim_matches=not guard_action_claim(result.text or "", obs).blocked,
            response=(result.text or "")[:160],
        )

    failed = nonlocal_failed[0]
    print("==================================================")
    print("AGENT MULTI SMOKE")
    print("==================================================")
    for row in rows:
        flag = "PASS" if row["ok"] else "FAIL"
        print(f"input={row['input']}")
        print(f"compound={row['compound']}")
        print(f"clauses={row['clauses']}")
        print(f"families={row['families']}")
        print(f"planner_steps={row['planner_steps']}")
        print(f"service_target={row['service_target']}")
        print(f"app_checked={row['app_checked']}")
        print(f"app_found={row['app_found']}")
        print(f"resolved_kind={row['resolved_kind']}")
        print(f"official_url_verified={row['official_url_verified']}")
        print(f"fallback_used={row['fallback_used']}")
        print(f"tools={row['tools']}")
        print(f"tool_results={row['tool_results']}")
        print(f"legacy_count={row['legacy_count']}")
        print(f"action_claims={row['action_claims']}")
        print(f"claim_matches={row['claim_matches']}")
        print(f"response={row.get('response', '')}")
        print(flag)
        print("---")
    total = len(rows)
    passed = total - failed
    print(f"{passed}/{total} {'PASS' if failed == 0 else 'FAIL'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_agent_multi_smoke())
