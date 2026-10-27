# ==========================
# ELIAN — Jarvis Behavioral corpora assembly (P2)
# ==========================
from __future__ import annotations

from collections import Counter
from typing import List, Sequence

from core.jarvis_behavioral.corpus.multi_turn import build_multi_turn
from core.jarvis_behavioral.corpus.noisy import build_noisy
from core.jarvis_behavioral.corpus.novel import build_novel_corpus, novel_overlap_report
from core.jarvis_behavioral.corpus.poison import build_poison
from core.jarvis_behavioral.corpus.regression import build_regression
from core.jarvis_behavioral.corpus.single_turn import build_single_turn
from core.jarvis_behavioral.types import Scenario

DEFAULT_SEED = 42


def build_corpus(
    *,
    seed: int = DEFAULT_SEED,
    mode: str = "full",
) -> List[Scenario]:
    """Assemble scenarios for the requested runner mode."""
    mode = (mode or "full").strip().lower().replace("_", "-")
    rows: List[Scenario] = []

    if mode in ("explore", "novel", "jarvis-explore"):
        base = build_corpus(seed=DEFAULT_SEED, mode="full")
        novel = build_novel_corpus(seed=seed if seed != DEFAULT_SEED else 20260912, existing=base)
        return _dedupe(novel)

    if mode == "critical":
        rows.extend(build_regression(seed=seed))
        rows.extend([s for s in build_multi_turn(seed=seed, minimum=200) if s.critical])
        rows.extend([s for s in build_poison(seed=seed, minimum=750) if s.poison_critical])
        return _dedupe(rows)

    if mode == "multi-turn":
        return _dedupe(build_multi_turn(seed=seed, minimum=200))

    if mode == "poison":
        return _dedupe(build_poison(seed=seed, minimum=750))

    # full (default)
    rows.extend(build_regression(seed=seed))
    rows.extend(build_single_turn(seed=seed, minimum=1500))
    rows.extend(build_noisy(seed=seed, minimum=500))
    rows.extend(build_multi_turn(seed=seed, minimum=200))
    rows.extend(build_poison(seed=seed, minimum=750))
    return _dedupe(rows)


def _dedupe(rows: Sequence[Scenario]) -> List[Scenario]:
    seen = set()
    out: List[Scenario] = []
    for s in rows:
        if s.id in seen:
            continue
        seen.add(s.id)
        out.append(s)
    return out


def corpus_stats(rows: Sequence[Scenario]) -> dict:
    cats = Counter(s.category for s in rows)
    return {
        "total": len(rows),
        "by_category": dict(cats),
        "critical": sum(1 for s in rows if s.critical),
        "false_success_safety": sum(1 for s in rows if s.false_success_safety),
        "poison_critical": sum(1 for s in rows if s.poison_critical),
        "deterministic_core": sum(1 for s in rows if s.deterministic_core),
        "multi_turn": sum(1 for s in rows if s.category == "multi_turn"),
        "single_turn": sum(1 for s in rows if s.category == "single_turn"),
        "noisy": sum(1 for s in rows if s.category == "noisy"),
        "poison": sum(1 for s in rows if s.category == "poison"),
        "regression": sum(1 for s in rows if s.category == "regression"),
        "novel_single": sum(1 for s in rows if s.category == "novel_single"),
        "novel_multi": sum(1 for s in rows if s.category == "novel_multi"),
        "novel_poison": sum(1 for s in rows if s.category == "novel_poison"),
        "novel_compound": sum(1 for s in rows if s.category == "novel_compound"),
    }


def build_explore_bundle(*, behavioral_seed: int = DEFAULT_SEED, novel_seed: int = 20260912) -> dict:
    """Regression corpus + novel sets for --jarvis-explore reporting."""
    regression = build_corpus(seed=behavioral_seed, mode="full")
    novel = build_novel_corpus(seed=novel_seed, existing=regression)
    return {
        "regression": regression,
        "novel": novel,
        "overlap": novel_overlap_report(novel, regression),
        "regression_stats": corpus_stats(regression),
        "novel_stats": corpus_stats(novel),
    }
