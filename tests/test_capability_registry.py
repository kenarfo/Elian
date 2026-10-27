# ==========================
# ELIAN — Capability Registry tests
# ==========================
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.capability_registry import (
    BROWSER_AUTOMATION,
    CANONICAL_IDS,
    CapStatus,
    HOME_ASSISTANT,
    WEB_FETCH,
    WEB_SEARCH,
    compact_capabilities_for_planner,
    filter_catalog_by_capabilities,
    get_capability_registry,
    inject_capabilities_hint,
    reset_capability_registry,
)
from core.intent_family import IntentClassification, IntentFamily
from core.tool_result import ToolResult, ToolStatus
from core.tool_shortlist import goal_needs_shortlist_tools, shortlist_tools
from core.tools import Tool, ToolRegistry, reset_registry_for_tests
from core.risk import RiskLevel


def _catalog(*names: str):
    return [
        {
            "name": name,
            "description": name,
            "parameters": {},
            "required": [],
            "properties": {},
            "risk_level": "safe",
            "available": True,
        }
        for name in names
    ]


class CapabilityRegistryTests(unittest.TestCase):
    def setUp(self):
        reset_capability_registry()
        reset_registry_for_tests()

    def tearDown(self):
        reset_capability_registry()
        reset_registry_for_tests()

    def test_canonical_capabilities_exist(self):
        reg = get_capability_registry()
        snap = reg.snapshot(force=True)
        for cid in CANONICAL_IDS:
            self.assertIn(cid, snap)
            self.assertIn("status", snap[cid])
            self.assertIn("tools", snap[cid])

    def test_system_family_is_alias_not_new_cap(self):
        from core.capability_registry import COMPUTER_CONTROL

        reg = get_capability_registry()
        snap = reg.snapshot(force=True)
        self.assertNotIn("SYSTEM", CANONICAL_IDS)
        self.assertNotIn("SYSTEM", snap)
        rec = reg.get("SYSTEM")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.id, COMPUTER_CONTROL)
        self.assertEqual(reg.ids_for_family("SYSTEM"), (COMPUTER_CONTROL, "WINDOWS_APPS"))
        internet = reg.get("INTERNET")
        self.assertIsNotNone(internet)
        self.assertEqual(internet.id, WEB_SEARCH)
        rows = compact_capabilities_for_planner(intent_family="SYSTEM")
        ids = [str(row.get("id") or "") for row in rows]
        self.assertIn(COMPUTER_CONTROL, ids)

    def test_web_search_available_when_provider_ready(self):
        reg = get_capability_registry()
        with patch(
            "core.web_search_provider.provider_available", return_value=True
        ):
            reg.refresh(force=True)
        rec = reg.get(WEB_SEARCH)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, CapStatus.AVAILABLE)
        self.assertIn("web_search", rec.tools)

    def test_web_search_unavailable_provider(self):
        reg = get_capability_registry()
        with patch(
            "core.web_search_provider.provider_available", return_value=False
        ):
            reg.refresh(force=True)
        rec = reg.get(WEB_SEARCH)
        self.assertEqual(rec.status, CapStatus.UNAVAILABLE)
        msg = rec.spoken_failure().lower()
        self.assertTrue(
            "nie mogę" in msg or "nie mam" in msg
        )
        self.assertNotIn("nie potrafię", msg)

    def test_ha_disabled_message(self):
        reg = get_capability_registry()
        with patch("Config.get_config_value", side_effect=lambda *a, **k: False):
            # Force only smart_home path — set directly for clarity
            pass
        reg._set(HOME_ASSISTANT, CapStatus.DISABLED, reason="smart_home disabled")
        msg = reg.get(HOME_ASSISTANT).spoken_failure()
        self.assertIn("wyłączona", msg.lower())

    def test_ha_unavailable_dependency_language(self):
        reg = get_capability_registry()
        reg._set(
            HOME_ASSISTANT,
            CapStatus.UNAVAILABLE,
            reason="connection refused",
        )
        msg = reg.get(HOME_ASSISTANT).spoken_failure().lower()
        self.assertTrue(
            "nie mogę teraz połączyć" in msg or "home assistant" in msg
        )
        self.assertNotIn("nie potrafię", msg)

    def test_browser_disabled_by_default(self):
        reg = get_capability_registry()
        with patch("core.browser_agent.browser_opt_in", return_value=False):
            reg.refresh(force=True)
        self.assertEqual(reg.get(BROWSER_AUTOMATION).status, CapStatus.DISABLED)

    def test_degraded_still_usable(self):
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.DEGRADED, reason="slow provider")
        self.assertTrue(reg.get(WEB_SEARCH).usable)
        self.assertIsNone(reg.gate_tool("web_search"))

    def test_unavailable_gates_invoke(self):
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.UNAVAILABLE, reason="provider down")
        # Freeze TTL so refresh doesn't overwrite
        reg._last_refresh = 10**12
        gated = reg.gate_tool("web_search")
        self.assertIsNotNone(gated)
        self.assertEqual(gated.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(gated.error_code, "CAPABILITY_UNAVAILABLE")
        self.assertFalse(gated.success)

    def test_filter_drops_unavailable_adds_alternative(self):
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.UNAVAILABLE, reason="down")
        reg._set(WEB_FETCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12
        catalog = _catalog("web_search", "fetch_web_page", "open_website", "open_app")
        filtered = filter_catalog_by_capabilities(catalog)
        names = [item["name"] for item in filtered]
        self.assertNotIn("web_search", names)
        self.assertIn("fetch_web_page", names)
        self.assertIn("open_website", names)

    def test_shortlist_selects_web_search_when_available(self):
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.AVAILABLE)
        reg._set(WEB_FETCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12
        catalog = _catalog(
            "web_search",
            "fetch_web_page",
            "open_website",
            "open_app",
            "play_media",
        )
        classification = IntentClassification(
            family=IntentFamily.WEB,
            is_complete=True,
            slots={},
        )
        selected = shortlist_tools(
            catalog,
            goal="wyszukaj aktualnego prezydenta USA",
            classification=classification,
        )
        names = [item["name"] for item in selected]
        self.assertIn("web_search", names)
        self.assertEqual(names[0], "web_search")

    def test_shortlist_wrong_tool_not_preferred_for_web(self):
        catalog = _catalog("web_search", "open_app", "play_media")
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12
        classification = IntentClassification(
            family=IntentFamily.WEB,
            is_complete=True,
            slots={},
        )
        selected = shortlist_tools(
            catalog,
            goal="szukaj w internecie pogody w Warszawie",
            classification=classification,
        )
        names = [item["name"] for item in selected]
        self.assertIn("web_search", names)
        # open_app must not crowd out web for WEB family
        if "open_app" in names:
            self.assertLess(names.index("web_search"), names.index("open_app"))


    def test_shortlist_includes_web_when_cap_available_despite_stale_flag(self):
        """Cap AVAILABLE + registered tool must shortlist even if Tool.available stale False."""
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.AVAILABLE, detail="provider ready")
        reg._set(WEB_FETCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12
        catalog = _catalog("web_search", "fetch_web_page", "open_website", "open_app")
        for item in catalog:
            if item["name"] == "web_search":
                item["available"] = False
        classification = IntentClassification(
            family=IntentFamily.WEB,
            is_complete=True,
            slots={},
        )
        selected = shortlist_tools(
            catalog,
            goal="wyszukaj Elon Musk",
            classification=classification,
        )
        names = [item["name"] for item in selected]
        self.assertIn("web_search", names)

    def test_shortlist_not_empty_when_needs_tools_false_but_cap_web_available(self):
        """Planner talk_only from stale needs_tools=False must not drop Cap-AVAILABLE web tools."""
        from core.semantic_executive import DecisionEnvelope, DecisionMode

        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.AVAILABLE, detail="provider ready")
        reg._set(WEB_FETCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12
        catalog = _catalog("web_search", "fetch_web_page", "open_website")
        classification = IntentClassification(
            family=IntentFamily.WEB,
            is_complete=True,
            slots={},
        )
        envelope = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="web",
            needs_tools=False,
            allowed_domains=["WEB"],
        )
        self.assertTrue(
            goal_needs_shortlist_tools(
                "wyszukaj X", classification, envelope=envelope
            )
        )
        selected = shortlist_tools(
            catalog,
            goal="wyszukaj X",
            classification=classification,
            talk_only=True,
            envelope=envelope,
        )
        names = [item["name"] for item in selected]
        self.assertIn("web_search", names)
        self.assertTrue(names)

    def test_planner_receives_compact_capabilities(self):

        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12
        caps = compact_capabilities_for_planner(
            goal="wyszukaj newsy",
            intent_family="WEB",
            tool_names=["web_search"],
        )
        self.assertTrue(caps)
        ids = [c["id"] for c in caps]
        self.assertIn(WEB_SEARCH, ids)
        row = next(c for c in caps if c["id"] == WEB_SEARCH)
        self.assertEqual(row["status"], CapStatus.AVAILABLE.value)
        self.assertNotIn("parameters", row)
        self.assertIn("tools", row)

    def test_tool_result_contract_fields(self):
        ok = ToolResult.ok("ok", tool_name="web_search", side_effect=False)
        self.assertTrue(ok.success)
        self.assertEqual(ok.status, ToolStatus.SUCCESS)
        obs = ok.to_observation()
        self.assertIn("error_code", obs)
        self.assertIn("side_effect", obs)

        fail = ToolResult.fail("x", error_code="FAILURE", retryable=False)
        self.assertEqual(fail.error_code, "FAILURE")
        self.assertNotEqual(fail.status, ToolStatus.UNAVAILABLE)

        unavail = ToolResult.unavailable(
            "Nie mogę teraz połączyć się z Home Assistant.",
            tool_name="turn_on_device",
            error_code="CAPABILITY_UNAVAILABLE",
        )
        self.assertEqual(unavail.status, ToolStatus.UNAVAILABLE)
        self.assertEqual(unavail.error_code, "CAPABILITY_UNAVAILABLE")
        self.assertFalse(unavail.side_effect)

    def test_registry_invoke_gates_unavailable_capability(self):
        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.UNAVAILABLE, reason="down")
        reg._last_refresh = 10**12

        tools = ToolRegistry(confirm_medium=False)
        called = {"n": 0}

        def handler(_args):
            called["n"] += 1
            return ToolResult.ok("should not run", tool_name="web_search")

        tools.register(
            Tool(
                name="web_search",
                description="search",
                handler=handler,
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        )
        result = tools.invoke("web_search", {"query": "test"})
        self.assertEqual(called["n"], 0)
        self.assertEqual(result.status, ToolStatus.UNAVAILABLE)
        self.assertTrue(
            "nie mogę" in result.message.lower() or "nie mam" in result.message.lower()
        )

    def test_inject_capabilities_hint_minimal(self):
        class _Env:
            user_goal = "szukaj"
            allowed_domains = ["WEB"]

        env = _Env()
        out = inject_capabilities_hint(env, goal="szukaj", intent_family="WEB")
        self.assertIs(out, env)
        self.assertTrue(getattr(env, "capability_hint", None))


class CapabilityHaUnavailableTests(unittest.TestCase):
    def setUp(self):
        reset_capability_registry()

    def test_ha_unavailable_gate_message(self):
        reg = get_capability_registry()
        reg._set(HOME_ASSISTANT, CapStatus.UNAVAILABLE, reason="offline")
        reg._last_refresh = 10**12
        gated = reg.gate_tool("turn_on_device")
        self.assertIsNotNone(gated)
        self.assertEqual(gated.status, ToolStatus.UNAVAILABLE)
        self.assertTrue(
            "nie mogę" in gated.message.lower() or "nie mam" in gated.message.lower()
        )
        self.assertNotIn("nie potrafię", gated.message.lower())




class PlannerShortlistCapAvailableTests(unittest.TestCase):
    """Planner path: Cap AVAILABLE + web intent must surface web_search."""

    def setUp(self):
        reset_capability_registry()
        reset_registry_for_tests()

    def tearDown(self):
        reset_capability_registry()
        reset_registry_for_tests()

    def test_build_messages_shortlist_web_search_when_cap_available(self):
        from core.model_client import FakeModelClient
        from core.planner import Planner
        from core.semantic_executive import DecisionEnvelope, DecisionMode

        reg = get_capability_registry()
        reg._set(WEB_SEARCH, CapStatus.AVAILABLE, detail="provider ready")
        reg._set(WEB_FETCH, CapStatus.AVAILABLE)
        reg._last_refresh = 10**12

        tools = ToolRegistry(confirm_medium=False)
        tools.register(
            Tool(
                name="web_search",
                description="Search the web",
                handler=lambda arguments: ToolResult.ok(
                    "ok", tool_name="web_search", data={"results": [], "result_count": 0}
                ),
                risk_level=RiskLevel.SAFE,
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                available=False,  # stale until Cap sync
            )
        )
        tools.register(
            Tool(
                name="fetch_web_page",
                description="Fetch",
                handler=lambda arguments: ToolResult.ok("ok", tool_name="fetch_web_page"),
                risk_level=RiskLevel.SAFE,
                parameters={"type": "object", "properties": {}},
                available=True,
            )
        )
        reset_registry_for_tests(tools)
        planner = Planner(tools, model=FakeModelClient([]))
        classification = IntentClassification(
            family=IntentFamily.WEB, is_complete=True, slots={}
        )
        envelope = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="web",
            needs_tools=False,
            allowed_domains=["WEB"],
        )
        messages = planner._build_messages(
            "wyszukaj Elon Musk",
            {"decision_envelope": envelope},
            [],
            0,
            classification=classification,
        )
        import json
        payload = None
        for msg in messages:
            content = msg.get("content") if isinstance(msg, dict) else ""
            if not isinstance(content, str):
                continue
            start = content.find("{")
            if start < 0:
                continue
            try:
                candidate = json.loads(content[start:])
            except Exception:
                continue
            if isinstance(candidate, dict) and "tools" in candidate:
                payload = candidate
                break
        self.assertIsNotNone(payload)
        tool_names = [str(item.get("name") or "") for item in (payload.get("tools") or [])]
        self.assertIn("web_search", tool_names)
        self.assertTrue(tools.get("web_search").available)
        catalog = tools.descriptions_for_planner()
        selected = shortlist_tools(
            catalog,
            goal="wyszukaj Elon Musk",
            classification=classification,
            talk_only=True,
            envelope=envelope,
        )
        names = [item["name"] for item in selected]
        self.assertIn("web_search", names)


if __name__ == "__main__":
    unittest.main()
