# ==========================
# Agent-first routing + universal devices
# ==========================
"""Regression: soft/HA speech prefers Agent; FastPath only when obvious."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.semantic_executive import (
    CONFIDENCE_STRONG,
    DecisionEnvelope,
    DecisionMode,
    fast_path_permitted,
    is_obvious_fast_path_intent,
)


class AgentFirstFastPathTests(unittest.TestCase):
    def test_usable_confidence_not_enough_for_fast_path(self):
        env = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="smart_home_control",
            confidence=0.80,
            needs_tools=True,
            allowed_domains=["SMART_HOME"],
        )
        self.assertFalse(fast_path_permitted("włącz lampę", env))

    def test_strong_volume_is_obvious(self):
        env = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="system_volume",
            confidence=CONFIDENCE_STRONG,
            needs_tools=True,
            allowed_domains=["SYSTEM"],
        )
        self.assertTrue(fast_path_permitted("zrób głośniej", env))
        self.assertTrue(is_obvious_fast_path_intent(env))

    def test_smart_home_complete_command_can_be_obvious(self):
        env = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="smart_home_control",
            confidence=0.95,
            needs_tools=True,
            allowed_domains=["SMART_HOME"],
        )
        # Vague soft desire is not obvious without a complete parse.
        self.assertFalse(is_obvious_fast_path_intent(env, "jest trochę ciemno"))

    def test_volume_intent_is_obvious(self):
        env = DecisionEnvelope(
            mode=DecisionMode.ACT.value,
            intent="system_volume",
            confidence=CONFIDENCE_STRONG,
            needs_tools=True,
            allowed_domains=["SYSTEM"],
        )
        self.assertTrue(is_obvious_fast_path_intent(env, "zrób głośniej"))


class DeviceCatalogTests(unittest.TestCase):
    def test_alias_resolve(self):
        from Config import load_config

        load_config()
        from core.device_catalog import resolve_alias

        self.assertEqual(resolve_alias("lampa"), "light.vizzini")
        self.assertEqual(resolve_alias("vizzini"), "light.vizzini")


if __name__ == "__main__":
    unittest.main()
