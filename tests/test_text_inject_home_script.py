# ==========================
# ELIAN — text inject + Fake HA home multi-turn
# ==========================
"""Regression: dry harness must accept production HA args and mutate fake state."""

from __future__ import annotations

import unittest

from core.fake_ha_backend import FakeHaBackend, reset_fake_ha_backend
from core.model_client import reset_model_client
from core.text_inject import _make_manager, run_one_turn
from core.tools import reset_registry_for_tests


class FakeHaBackendUnitTests(unittest.TestCase):
    def test_adjust_brightness_mutates_state(self):
        backend = FakeHaBackend()
        backend.reset()
        self.assertEqual(backend.snapshot("light.vizzini")["state"], "off")
        result = backend.handle(
            "set_device_value",
            {
                "action": "adjust_brightness",
                "domain": "light",
                "entity_id": "light.vizzini",
                "target": "light.vizzini",
                "value": 20,
            },
        )
        self.assertTrue(result.success)
        snap = backend.snapshot("light.vizzini")
        self.assertEqual(snap["state"], "on")
        self.assertGreaterEqual(int(snap["brightness_pct"]), 20)


class TextInjectHomeScriptTests(unittest.TestCase):
    def tearDown(self):
        reset_model_client()
        reset_registry_for_tests(None)
        reset_fake_ha_backend()

    def test_ciemno_tu_no_schema_reject(self):
        mgr = _make_manager(live_tools=False)
        try:
            row = run_one_turn(mgr, "ciemno tu", debug=False)
            text = (row.get("text") or "").lower()
            self.assertNotIn("niedozwolone argumenty", text)
            tools = row.get("TOOL") or []
            self.assertTrue(tools, msg=f"expected HA tool call, got {row}")
            self.assertTrue(
                any(t.get("tool") == "set_device_value" and t.get("success") for t in tools),
                msg=f"set_device_value should succeed: {tools}",
            )
            ha = row.get("WORLDSTATE_HA") or {}
            self.assertEqual(ha.get("state"), "on")
            self.assertGreater(int(ha.get("brightness_pct") or 0), 0)
        finally:
            mgr.deactivate(reason="test_end")
            tmp = getattr(mgr, "_text_inject_tmp", None)
            if tmp is not None:
                tmp.cleanup()

    def test_multi_turn_brightness_then_off(self):
        mgr = _make_manager(live_tools=False)
        try:
            turns = [
                "ciemno tu",
                "trochę jaśniej",
                "nie aż tak",
                "zgaś ją",
            ]
            rows = [run_one_turn(mgr, t, debug=False) for t in turns]
            for row in rows:
                self.assertNotIn(
                    "niedozwolone argumenty",
                    (row.get("text") or "").lower(),
                    msg=row,
                )
            # First turn brightens; last turn should turn off (or attempt light_off).
            first_tools = rows[0].get("TOOL") or []
            self.assertTrue(any(t.get("success") for t in first_tools), rows[0])
            last = rows[-1]
            last_text = (last.get("text") or "").lower()
            last_tools = last.get("TOOL") or []
            ha = last.get("WORLDSTATE_HA") or {}
            off_ok = ha.get("state") == "off" or any(
                t.get("tool") == "turn_off_device" and t.get("success") for t in last_tools
            )
            self.assertTrue(
                off_ok or "zgas" in last_text or "wyłącz" in last_text,
                msg=f"expected light off path: {last}",
            )
        finally:
            mgr.deactivate(reason="test_end")
            tmp = getattr(mgr, "_text_inject_tmp", None)
            if tmp is not None:
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
