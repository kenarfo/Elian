# ==========================
# ELIAN — STT-like / noisy corpus (P2)
# ==========================
from __future__ import annotations

from typing import List

from core.jarvis_behavioral.corpus.single_turn import build_single_turn
from core.jarvis_behavioral.corpus.templates import noisy_variants, seeded_rng, stable_id
from core.jarvis_behavioral.types import Scenario, TurnSpec

SEED = 42


def build_noisy(*, seed: int = SEED, minimum: int = 500) -> List[Scenario]:
    rng = seeded_rng(seed + 7)
    base = build_single_turn(seed=seed, minimum=400)
    # Prefer actionable domains for noisy variants
    pool = [s for s in base if s.domain in ("HOME", "MEDIA", "APPS", "WEB", "SYSTEM", "REMINDERS", "MIXED")]
    rows: List[Scenario] = []
    seen = set()
    for src in pool:
        text = src.turns[0].user
        for variant in noisy_variants(text, rng, n=4):
            key = variant.casefold()
            if key in seen:
                continue
            seen.add(key)
            turn0 = src.turns[0]
            rows.append(
                Scenario(
                    id=stable_id("noisy", variant, seed),
                    category="noisy",
                    domain=src.domain,
                    turns=[
                        TurnSpec(
                            user=variant,
                            expect_mode=turn0.expect_mode,
                            expect_tool_any=turn0.expect_tool_any,
                            expect_no_side_effect=turn0.expect_no_side_effect,
                            expect_ask=turn0.expect_ask,
                            notes="stt_like",
                        )
                    ],
                    score_bucket=src.score_bucket,
                    tags=("noisy", "stt", src.domain.lower()),
                    seed=seed,
                    deterministic_core=False,
                    world=dict(src.world),
                    expected_behavior=src.expected_behavior,
                )
            )
            if len(rows) >= minimum:
                return rows
    # Hand noisy extras
    extras = [
        ("HOME", "wez no wlacz to"),
        ("APPS", "no ten spotify odpal"),
        ("HOME", "jasniej znaczy nie tak bardzo"),
        ("WEB", "wyszukaj mi no ten tego o czym gadalismy"),
        ("APPS", "zamk znaczy nie dobra zostaw"),
        ("REMINDERS", "przypomnij mi o tym no pozniej jakos"),
        ("MEDIA", "pusc cos michala"),
        ("SYSTEM", "ciszej wez no"),
        ("HOME", "zgas ja"),
        ("HOME", "ciemno tu yyy"),
    ]
    from core.jarvis_behavioral.types import ExpectMode

    mode_map = {
        "HOME": ExpectMode.ACT,
        "APPS": ExpectMode.ACT,
        "WEB": ExpectMode.ACT,
        "REMINDERS": ExpectMode.ACT,
        "MEDIA": ExpectMode.ACT,
        "SYSTEM": ExpectMode.ACT,
    }
    for domain, phrase in extras:
        if phrase.casefold() in seen:
            continue
        rows.append(
            Scenario(
                id=stable_id("noisy_hand", phrase, seed),
                category="noisy",
                domain=domain,
                turns=[TurnSpec(user=phrase, expect_mode=mode_map[domain])],
                score_bucket="ACTION_REQUEST",
                tags=("noisy", "hand"),
                seed=seed,
                world={"unique_light": True} if domain == "HOME" else {},
                expected_behavior=f"ACT despite STT noise ({domain})",
            )
        )
    while len(rows) < minimum:
        src = rng.choice(pool)
        variant = noisy_variants(src.turns[0].user, rng, n=1)[0]
        if variant.casefold() in seen:
            # force diacritic strip
            from core.jarvis_behavioral.corpus.templates import strip_diacritics

            variant = strip_diacritics(src.turns[0].user) + f" {rng.randint(1, 999)}"
        seen.add(variant.casefold())
        rows.append(
            Scenario(
                id=stable_id("noisy_pad", variant, seed),
                category="noisy",
                domain=src.domain,
                turns=[
                    TurnSpec(
                        user=variant,
                        expect_mode=src.turns[0].expect_mode,
                        expect_tool_any=src.turns[0].expect_tool_any,
                    )
                ],
                score_bucket=src.score_bucket,
                tags=("noisy",),
                seed=seed,
                world=dict(src.world),
                expected_behavior=src.expected_behavior,
            )
        )
    return rows[:minimum]
