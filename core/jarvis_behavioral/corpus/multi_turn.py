# ==========================
# ELIAN — multi-turn dialogue corpus (P2)
# ==========================
from __future__ import annotations

from typing import List, Sequence, Tuple

from core.jarvis_behavioral.corpus.templates import seeded_rng, stable_id
from core.jarvis_behavioral.types import ExpectMode, Scenario, TurnSpec

SEED = 42

TurnTuple = Tuple[str, ExpectMode]


def _sc(
    sid: str,
    bucket: str,
    turns: Sequence[TurnSpec],
    *,
    tags: Tuple[str, ...] = (),
    critical: bool = False,
    prior_bug: str = "",
    world: dict | None = None,
    seed: int = SEED,
) -> Scenario:
    return Scenario(
        id=sid,
        category="multi_turn",
        turns=list(turns),
        score_bucket=bucket,
        tags=("multi_turn",) + tuple(tags),
        seed=seed,
        critical=critical,
        deterministic_core=True,
        world=world or {},
        expected_behavior=bucket + " multi-turn",
        prior_bug=prior_bug,
    )


def _hand_designed(seed: int) -> List[Scenario]:
    rows: List[Scenario] = []

    # REFERENCE / light continuum
    rows.append(
        _sc(
            "mt_ref_light_1",
            "REFERENCE",
            [
                TurnSpec("ciemno tu", ExpectMode.ACT, expect_tool_any=("turn_on_device", "set_device_value"), expect_ask=False),
                TurnSpec("jaśniej", ExpectMode.ACT, expect_tool_any=("set_device_value", "turn_on_device")),
                TurnSpec("nie aż tak", ExpectMode.ACT, expect_tool_any=("set_device_value",)),
                TurnSpec("zgaś ją", ExpectMode.ACT, expect_tool_any=("turn_off_device",), forbid_tools=("set_device_value",)),
            ],
            tags=("reference", "home"),
            critical=True,
            prior_bug="zgas_ja_brightness",
            world={"unique_light": True, "no_ask": True},
            seed=seed,
        )
    )

    # CHOICE without goal pollution
    rows.append(
        _sc(
            "mt_choice_web_1",
            "CORRECTION",
            [
                TurnSpec("wyszukaj coś o Michaelu Jacksonie", ExpectMode.ACT, expect_tool_any=("web_search",)),
                TurnSpec("otwórz pierwszy", ExpectMode.ACT, expect_tool_any=("open_website", "open_app", "web_search")),
                TurnSpec("nie, drugi", ExpectMode.ACT, expect_tool_any=("open_website", "open_app", "web_search")),
                TurnSpec("co tam piszą?", ExpectMode.INFO, expect_no_side_effect=True),
            ],
            tags=("choice", "web"),
            critical=True,
            prior_bug="goal_pollution",
            seed=seed,
        )
    )

    # RETRY / WHY after failure inject
    rows.append(
        _sc(
            "mt_retry_fail_1",
            "RETRY",
            [
                TurnSpec("otwórz Spotify", ExpectMode.ACT, expect_tool_any=("open_app",), inject="fail"),
                TurnSpec("czemu nie zrobiłeś?", ExpectMode.INFO, expect_no_side_effect=True),
                TurnSpec("spróbuj jeszcze raz", ExpectMode.RETRY, expect_tool_any=("open_app",), inject="ok"),
                TurnSpec("ale inaczej", ExpectMode.INFO, expect_no_side_effect=True),
            ],
            tags=("retry", "replan", "why"),
            critical=True,
            seed=seed,
        )
    )

    # CANCEL soft-resume
    rows.append(
        _sc(
            "mt_cancel_soft_1",
            "CORRECTION",
            [
                TurnSpec("otwórz Spotify", ExpectMode.ACT, expect_tool_any=("open_app",)),
                TurnSpec("nieważne", ExpectMode.CANCEL, expect_no_side_effect=True),
                TurnSpec("no to zrób", ExpectMode.NO_ACTION, expect_no_side_effect=True),
            ],
            tags=("cancel",),
            critical=True,
            prior_bug="cancel_soft_resume",
            seed=seed,
        )
    )

    # CONTEXT SWITCH light → volume
    rows.append(
        _sc(
            "mt_ctx_light_vol_1",
            "CONTEXT",
            [
                TurnSpec("jaśniej", ExpectMode.ACT, expect_tool_any=("set_device_value", "turn_on_device")),
                TurnSpec("trochę ciszej", ExpectMode.ACT, expect_tool_any=("volume_down",), forbid_tools=("set_device_value", "turn_on_device", "turn_off_device")),
            ],
            tags=("context_switch",),
            critical=True,
            prior_bug="light_volume",
            world={"unique_light": True},
            seed=seed,
        )
    )

    # PARTIAL compound
    rows.append(
        _sc(
            "mt_partial_compound_1",
            "MULTI_TOOL",
            [
                TurnSpec(
                    "otwórz Spotify i zgaś lampę",
                    ExpectMode.PARTIAL_SUCCESS,
                    inject="partial",
                ),
            ],
            tags=("partial", "compound"),
            critical=True,
            prior_bug="compound_partial",
            world={"partial_fail_tools": ["turn_off_device"], "unique_light": True},
            seed=seed,
        )
    )

    # MEMORY reminder from context
    rows.append(
        _sc(
            "mt_mem_reminder_1",
            "MEMORY",
            [
                TurnSpec("jutro chyba pójdę do sklepu", ExpectMode.TALK, expect_no_side_effect=True),
                TurnSpec("w sumie przypomnij mi o 16", ExpectMode.ACT, expect_tool_any=("create_reminder",)),
                TurnSpec("jednak nieważne", ExpectMode.CANCEL, expect_no_side_effect=True),
            ],
            tags=("memory", "reminder"),
            critical=True,
            seed=seed,
        )
    )

    # PARAMETER / correction
    rows.append(
        _sc(
            "mt_param_vol_1",
            "CORRECTION",
            [
                TurnSpec("podgłośnij", ExpectMode.ACT, expect_tool_any=("volume_up",)),
                TurnSpec("nie aż tak", ExpectMode.ACT, expect_tool_any=("volume_down",)),
                TurnSpec("jeszcze trochę ciszej", ExpectMode.ACT, expect_tool_any=("volume_down",)),
            ],
            tags=("parameter",),
            seed=seed,
        )
    )

    # MULTI-TOOL resume
    rows.append(
        _sc(
            "mt_multitool_1",
            "MULTI_TOOL",
            [
                TurnSpec("wyszukaj coś o jarvis", ExpectMode.ACT, expect_tool_any=("web_search",)),
                TurnSpec("otwórz pierwszy", ExpectMode.ACT),
                TurnSpec("i ciszej", ExpectMode.ACT, expect_tool_any=("volume_down",)),
            ],
            tags=("multi_tool",),
            seed=seed,
        )
    )

    # Clarify then new goal must not pollute
    rows.append(
        _sc(
            "mt_clarify_pollute_1",
            "CONTEXT",
            [
                TurnSpec("włącz światło", ExpectMode.ACT, expect_tool_any=("turn_on_device", "set_device_value")),
                TurnSpec("lampę", ExpectMode.ACT, expect_tool_any=("turn_on_device", "set_device_value")),
                TurnSpec("trochę ciszej", ExpectMode.ACT, expect_tool_any=("volume_down",), forbid_tools=("turn_on_device",)),
            ],
            tags=("clarify",),
            critical=True,
            prior_bug="clarify_pollution",
            world={"unique_light": True},
            seed=seed,
        )
    )

    return rows


def _generated(seed: int, need: int) -> List[Scenario]:
    rng = seeded_rng(seed + 99)
    templates = [
        (
            "REFERENCE",
            ("reference",),
            [
                ("zapal lampę", ExpectMode.ACT),
                ("jaśniej", ExpectMode.ACT),
                ("zgaś ją", ExpectMode.ACT),
            ],
        ),
        (
            "CORRECTION",
            ("correction",),
            [
                ("puść coś michaela", ExpectMode.ACT),
                ("nie to", ExpectMode.ACT),
                ("daj coś spokojniejszego", ExpectMode.ACT),
            ],
        ),
        (
            "RETRY",
            ("retry",),
            [
                ("otwórz Chrome", ExpectMode.ACT),
                ("spróbuj jeszcze raz", ExpectMode.RETRY),
                ("dobra nieważne", ExpectMode.CANCEL),
            ],
        ),
        (
            "CONTEXT",
            ("context_switch",),
            [
                ("wyszukaj pogodę", ExpectMode.ACT),
                ("trochę ciszej", ExpectMode.ACT),
                ("co tam?", ExpectMode.TALK),
            ],
        ),
        (
            "MEMORY",
            ("memory",),
            [
                ("zapamiętaj że wolę Spotify", ExpectMode.ACT),
                ("co wolę?", ExpectMode.INFO),
                ("zapomnij że wolę Spotify", ExpectMode.ACT),
            ],
        ),
        (
            "MULTI_TOOL",
            ("multi_tool",),
            [
                ("odpal spotify", ExpectMode.ACT),
                ("i ciszej", ExpectMode.ACT),
                ("co leci?", ExpectMode.INFO),
            ],
        ),
        (
            "REPLAN",
            ("replan",),
            [
                ("wyszukaj elon musk", ExpectMode.ACT),
                ("spróbuj inaczej", ExpectMode.REPLAN),
                ("otwórz pierwszy", ExpectMode.ACT),
            ],
        ),
        (
            "REFERENCE",
            ("resume",),
            [
                ("przypomnij mi o ładowarce", ExpectMode.ACT),
                ("ej jednak zrób", ExpectMode.ACT),
                ("już nie musisz", ExpectMode.CANCEL),
            ],
        ),
    ]
    apps = ["Spotify", "Chrome", "Discord", "Notatnik"]
    topics = ["Jarvis", "Michael Jackson", "pogodzie", "Gdańsku", "AI"]
    rows: List[Scenario] = []
    i = 0
    while len(rows) < need:
        bucket, tags, skeleton = templates[i % len(templates)]
        i += 1
        turns: List[TurnSpec] = []
        for text, mode in skeleton:
            t = text
            if "Chrome" in t or "spotify" in t.lower():
                t = t.replace("Chrome", rng.choice(apps)).replace("spotify", rng.choice(apps).lower())
            if "elon musk" in t or "pogodę" in t:
                t = t.replace("elon musk", rng.choice(topics)).replace("pogodę", rng.choice(topics))
            # Pad dialogues to 3–12 turns
            turns.append(TurnSpec(t, mode))
        # Occasionally extend with safe discourse turns (no strict ACT demands)
        extra_n = rng.randint(0, 3)
        for _ in range(extra_n):
            filler = rng.choice(["ok", "dzięki", "jasne", "spoko", "dobra"])
            turns.append(
                TurnSpec(
                    filler,
                    ExpectMode.TALK,
                    expect_no_side_effect=True,
                )
            )
        if len(turns) < 3:
            turns.append(TurnSpec("ok", ExpectMode.TALK, expect_no_side_effect=True))
        sid = stable_id(f"mt_gen_{bucket.lower()}", "|".join(t.user for t in turns), seed + i)
        rows.append(
            _sc(
                sid,
                bucket if bucket != "REPLAN" else "REPLAN",
                turns,
                tags=tags,
                seed=seed,
                world={"unique_light": True},
            )
        )
    # Generated dialogues are coverage, not deterministic-core gates.
    for row in rows:
        row.deterministic_core = False
        row.critical = False
    return rows


def build_multi_turn(*, seed: int = SEED, minimum: int = 200) -> List[Scenario]:
    hand = _hand_designed(seed)
    need = max(0, minimum - len(hand))
    gen = _generated(seed, need) if need else []
    rows = hand + gen
    return rows[: max(minimum, len(rows))]
