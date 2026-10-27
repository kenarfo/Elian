# ==========================
# ELIAN — SMART_HOME must not invent web_search
# ==========================
from __future__ import annotations

import unittest

from core.intent_family import IntentClassification, IntentFamily
from core.tool_compat import is_tool_compatible, repair_incompatible_tool
from core.text_inject import _make_manager, run_one_turn


class SmartHomeNoWebSearchTests(unittest.TestCase):
    def test_web_search_incompatible_with_smart_home(self):
        cls = IntentClassification(
            family=IntentFamily.SMART_HOME,
            is_complete=True,
            slots={"semantic_action": "turn_off"},
            raw_text="zgaś lampę",
        )
        self.assertFalse(
            is_tool_compatible("web_search", goal="zgaś lampę", classification=cls)
        )
        repaired = repair_incompatible_tool(
            "web_search",
            goal="zgaś lampę",
            classification=cls,
            arguments={"query": "placeholder"},
        )
        self.assertIsNotNone(repaired)
        self.assertTrue(repaired.is_tool_call)
        self.assertEqual(repaired.tool, "turn_off_device")

    def test_web_still_ok_for_web_family(self):
        cls = IntentClassification(
            family=IntentFamily.WEB,
            is_complete=True,
            slots={},
            raw_text="poszukaj pogody",
        )
        self.assertTrue(
            is_tool_compatible("web_search", goal="poszukaj pogody", classification=cls)
        )

    def test_inject_unknown_ha_paraphrase_no_web(self):
        """Even if soft misses, agent must not web_search a light command."""
        mgr = _make_manager(live_tools=False)
        try:
            run_one_turn(mgr, "włącz lampę", debug=False)
            # Force agent path with a slightly odd but SMART_HOME phrasing.
            row = run_one_turn(mgr, "odetnij zasilanie lampy", debug=False)
            tools = row.get("TOOL") or []
            names = [str(t.get("tool") or "") for t in tools if isinstance(t, dict)]
            text = str(row.get("FINAL_RESPONSE") or row.get("text") or "").lower()
            self.assertNotIn("web_search", names)
            self.assertNotIn("według źródeł", text)
            self.assertNotIn("placeholder", text)
        finally:
            try:
                mgr.deactivate(reason="test_end")
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
