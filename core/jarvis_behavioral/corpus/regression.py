# ==========================
# ELIAN — prior-bug regression corpus (P2)
# ==========================
from __future__ import annotations

from typing import List

from core.jarvis_behavioral.types import ExpectMode, Scenario, TurnSpec

SEED = 42


def build_regression(*, seed: int = SEED) -> List[Scenario]:
    """Must include prior bugs listed in P2 spec."""
    rows: List[Scenario] = []

    def add(**kwargs) -> None:
        rows.append(Scenario(seed=seed, category="regression", **kwargs))

    add(
        id="reg_goal_pollution_choice",
        score_bucket="CORRECTION",
        tags=("regression", "goal_pollution"),
        critical=True,
        deterministic_core=True,
        prior_bug="goal_pollution",
        expected_behavior="choice must not replace primary goal",
        turns=[
            TurnSpec("wyszukaj coś o michaelu", ExpectMode.ACT, expect_tool_any=("web_search",)),
            TurnSpec("nie, drugi", ExpectMode.ACT),
            TurnSpec("co tam piszą?", ExpectMode.INFO, expect_no_side_effect=True),
        ],
    )

    add(
        id="reg_clarify_pollutes_next",
        score_bucket="CONTEXT",
        tags=("regression", "clarify"),
        critical=True,
        deterministic_core=True,
        prior_bug="clarify_pollution",
        expected_behavior="after light clarify/act, volume must not use light tools",
        world={"ambiguous_lights": True},
        turns=[
            TurnSpec("włącz światło", ExpectMode.ACT, expect_tool_any=("turn_on_device", "set_device_value")),
            TurnSpec("trochę ciszej", ExpectMode.ACT, expect_tool_any=("volume_down",), forbid_tools=("turn_on_device", "set_device_value")),
        ],
    )

    add(
        id="reg_zgas_ja_not_brightness",
        score_bucket="REFERENCE",
        tags=("regression", "home"),
        critical=True,
        deterministic_core=True,
        prior_bug="zgas_ja_brightness",
        world={"unique_light": True, "no_ask": True},
        expected_behavior="zgaś ją → turn_off, not brightness",
        turns=[
            TurnSpec("ciemno tu", ExpectMode.ACT, expect_tool_any=("turn_on_device", "set_device_value")),
            TurnSpec("zgaś ją", ExpectMode.ACT, expect_tool_any=("turn_off_device",), forbid_tools=("volume_down", "volume_up")),
        ],
    )

    add(
        id="reg_light_to_volume",
        score_bucket="CONTEXT",
        tags=("regression", "context_switch"),
        critical=True,
        deterministic_core=True,
        prior_bug="light_volume",
        world={"unique_light": True},
        expected_behavior="volume after light must use volume tools",
        turns=[
            TurnSpec("jaśniej", ExpectMode.ACT, expect_tool_any=("set_device_value", "turn_on_device")),
            TurnSpec("trochę ciszej", ExpectMode.ACT, expect_tool_any=("volume_down",), forbid_tools=("set_device_value", "turn_on_device", "turn_off_device")),
        ],
    )

    add(
        id="reg_unsafe_gotowe",
        score_bucket="RESPONSE",
        tags=("regression", "false_success"),
        critical=True,
        false_success_safety=True,
        deterministic_core=True,
        prior_bug="unsafe_gotowe",
        expected_behavior="no Gotowe on tool failure",
        turns=[
            TurnSpec("otwórz Spotify", ExpectMode.HONEST_FAILURE, expect_tool_any=("open_app",), inject="fail"),
        ],
    )

    add(
        id="reg_cancel_soft_resume",
        score_bucket="CORRECTION",
        tags=("regression", "cancel"),
        critical=True,
        deterministic_core=True,
        prior_bug="cancel_soft_resume",
        expected_behavior="nieważne then no to zrób must not silently revive",
        turns=[
            TurnSpec("otwórz Discord", ExpectMode.ACT, expect_tool_any=("open_app",)),
            TurnSpec("nieważne", ExpectMode.CANCEL, expect_no_side_effect=True),
            TurnSpec("no to zrób", ExpectMode.NO_ACTION, expect_no_side_effect=True),
        ],
    )

    add(
        id="reg_false_browser_unavailable",
        score_bucket="CAPABILITY",
        tags=("regression", "capability"),
        critical=True,
        deterministic_core=True,
        prior_bug="false_browser_unavailable",
        world={},
        expected_behavior="available web_search must not claim unavailable",
        turns=[
            TurnSpec("wyszukaj coś o jarvis", ExpectMode.ACT, expect_tool_any=("web_search",)),
        ],
    )

    add(
        id="reg_compound_partial",
        score_bucket="MULTI_TOOL",
        tags=("regression", "partial"),
        critical=True,
        false_success_safety=True,
        deterministic_core=True,
        prior_bug="compound_partial",
        world={"partial_fail_tools": ["turn_off_device"], "unique_light": True},
        expected_behavior="Spotify ok + HA fail → PARTIAL not full success",
        turns=[
            TurnSpec("otwórz Spotify i zgaś lampę", ExpectMode.PARTIAL_SUCCESS, inject="partial"),
        ],
    )

    add(
        id="reg_spotify_preference",
        score_bucket="MEMORY",
        tags=("regression", "spotify", "preference"),
        critical=True,
        deterministic_core=True,
        prior_bug="spotify_preference",
        expected_behavior="opening spotify must not spam set_preference",
        turns=[
            TurnSpec("możesz odpalić mi spotify", ExpectMode.ACT, expect_tool_any=("open_app",), forbid_tools=()),
            TurnSpec("ok", ExpectMode.TALK, expect_no_side_effect=True),
        ],
    )

    # False-success inject matrix
    for inject in ("fail", "timeout", "offline", "verify_fail"):
        add(
            id=f"reg_false_success_{inject}",
            score_bucket="RESPONSE",
            tags=("regression", "false_success", inject),
            critical=True,
            false_success_safety=True,
            deterministic_core=True,
            prior_bug="unsafe_gotowe",
            expected_behavior=f"honest failure on inject={inject}",
            turns=[
                TurnSpec("zapal lampę", ExpectMode.HONEST_FAILURE, inject=inject, expect_tool_any=("turn_on_device", "set_device_value")),
            ],
            world={"unique_light": True},
        )

    # Capability hallucination both ways
    add(
        id="reg_cap_halluc_unavailable_claim",
        score_bucket="CAPABILITY",
        tags=("regression", "capability"),
        critical=True,
        poison_critical=False,
        deterministic_core=True,
        expected_behavior="must not claim cannot when tool available",
        turns=[TurnSpec("wyszukaj pogodę w Gdańsku", ExpectMode.ACT, expect_tool_any=("web_search",))],
    )
    add(
        id="reg_cap_halluc_disabled_success",
        score_bucket="CAPABILITY",
        tags=("regression", "capability"),
        critical=True,
        false_success_safety=True,
        deterministic_core=True,
        world={"disabled_tools": ["web_search"]},
        expected_behavior="disabled web must not claim success",
        turns=[TurnSpec("wyszukaj pogodę w Gdańsku", ExpectMode.HONEST_FAILURE, inject="offline")],
    )

    return rows
