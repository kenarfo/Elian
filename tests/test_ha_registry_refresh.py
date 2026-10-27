# ==========================
# ELIAN — HA registry fail-closed refresh
# ==========================
"""Network failure must not wipe fixture / previous entities."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import HomeAssistant as ha


class HaRegistryRefreshTests(unittest.TestCase):
    def tearDown(self):
        ha.get_registry().load_states([])

    def test_failed_refresh_keeps_fixture(self):
        reg = ha.get_registry()
        reg.load_states(
            [
                {
                    "entity_id": "light.vizzini",
                    "state": "on",
                    "attributes": {"friendly_name": "Vizzini", "brightness": 100},
                }
            ]
        )
        self.assertEqual(len(reg.all()), 1)
        self.assertTrue(reg._fixture_mode)

        def boom():
            return None

        with patch.object(reg, "_states_loader", boom):
            out = reg.refresh()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].entity_id, "light.vizzini")
        self.assertTrue(reg._fixture_mode)

    def test_fixture_maybe_refresh_skips_network(self):
        reg = ha.get_registry()
        reg.load_states(
            [
                {
                    "entity_id": "light.vizzini",
                    "state": "off",
                    "attributes": {"friendly_name": "Vizzini", "brightness": 0},
                }
            ]
        )
        calls = {"n": 0}

        def counted():
            calls["n"] += 1
            return None

        with patch.object(reg, "_states_loader", counted):
            reg.maybe_refresh(force=False)
            reg.maybe_refresh(force=True)  # still skipped without ELIAN_HA_LIVE_REFRESH
        self.assertEqual(calls["n"], 0)
        self.assertEqual(len(reg.all()), 1)

    def test_stale_hot_path_returns_cache_without_blocking(self):
        """Voice turn must not wait on ConnectTimeout when cache is warm."""
        reg = ha.HomeAssistantDeviceRegistry()
        reg.load_states(
            [
                {
                    "entity_id": "light.vizzini",
                    "state": "on",
                    "attributes": {"friendly_name": "Vizzini"},
                }
            ]
        )
        # Leave fixture mode so stale refresh would normally hit network.
        reg._fixture_mode = False
        reg._updated_at = time.monotonic() - 10_000.0
        slow_calls = {"n": 0}

        def slow_loader():
            slow_calls["n"] += 1
            time.sleep(0.35)
            return [
                {
                    "entity_id": "light.vizzini",
                    "state": "off",
                    "attributes": {"friendly_name": "Vizzini"},
                }
            ]

        with patch.object(reg, "_states_loader", slow_loader):
            t0 = time.perf_counter()
            out = reg.maybe_refresh(force=False)
            elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.15, msg=f"hot path blocked {elapsed:.3f}s")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].entity_id, "light.vizzini")
        # Background refresh was scheduled (may still be running).
        deadline = time.time() + 1.0
        while slow_calls["n"] == 0 and time.time() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(slow_calls["n"], 1)

    def test_force_refresh_still_sync(self):
        reg = ha.HomeAssistantDeviceRegistry()
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return [
                {
                    "entity_id": "switch.fan",
                    "state": "on",
                    "attributes": {"friendly_name": "Fan"},
                }
            ]

        with patch.object(reg, "_states_loader", loader):
            out = reg.maybe_refresh(force=True)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(out[0].entity_id, "switch.fan")

    def test_failed_refresh_cooldown_skips_sync_block(self):
        reg = ha.HomeAssistantDeviceRegistry()
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            return None

        with patch.object(reg, "_states_loader", boom):
            t0 = time.perf_counter()
            reg.maybe_refresh(force=False)  # never_loaded sync fail
            first = time.perf_counter() - t0
            t1 = time.perf_counter()
            reg.maybe_refresh(force=False)  # cooldown — must not sync-wait again
            second = time.perf_counter() - t1
        self.assertEqual(calls["n"], 1)
        self.assertLess(second, 0.15, msg=f"cooldown still blocked {second:.3f}s")
        self.assertGreaterEqual(first, 0.0)


if __name__ == "__main__":
    unittest.main()
