# ==========================
# ELIAN — Jarvis Behavioral Gate runner (P2)
# ==========================
from __future__ import annotations

import json
import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from core.jarvis_behavioral.corpus import build_corpus, corpus_stats
from core.jarvis_behavioral.evaluators import evaluate_scenario, evaluate_turn
from core.jarvis_behavioral.harness import capture_trace, jarvis_session
from core.jarvis_behavioral.scoring import THRESHOLDS, aggregate
from core.jarvis_behavioral.types import (
    GateSummary,
    Scenario,
    ScenarioResult,
    TurnTrace,
    Verdict,
)

ARTIFACT_DIR = Path(__file__).resolve().parents[2] / "Data" / "diagnostics" / "jarvis_behavioral"


def _parse_mode(argv: Optional[Sequence[str]] = None) -> str:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--jarvis-explore" in args or "--explore" in args:
        return "explore"
    if "--critical" in args:
        return "critical"
    if "--multi-turn" in args:
        return "multi-turn"
    if "--poison" in args:
        return "poison"
    return "full"


def _parse_seed(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    for i, a in enumerate(args):
        if a == "--seed" and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except ValueError:
                return 42
        if a.startswith("--seed="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                return 42
    return 42


def _to_turn_trace(raw: Dict[str, Any]) -> TurnTrace:
    return TurnTrace(
        input=str(raw.get("input") or ""),
        response=str(raw.get("response") or ""),
        source=str(raw.get("source") or ""),
        semantic_mode=str(raw.get("semantic_mode") or ""),
        semantic_intent=str(raw.get("semantic_intent") or ""),
        utterance_class=str(raw.get("utterance_class") or ""),
        active_goal=str(raw.get("active_goal") or ""),
        discourse=dict(raw.get("discourse") or {}),
        context=dict(raw.get("context") or {}),
        worldstate=dict(raw.get("worldstate") or {}),
        memory_keys=list(raw.get("memory_keys") or []),
        tools=list(raw.get("tools") or []),
        observations=list(raw.get("observations") or []),
        capabilities=dict(raw.get("capabilities") or {}),
        plan=str(raw.get("plan") or ""),
        verify=str(raw.get("verify") or ""),
        repair=str(raw.get("repair") or ""),
    )


def run_scenario(scenario: Scenario) -> ScenarioResult:
    world = dict(scenario.world or {})
    disabled = list(world.get("disabled_tools") or [])
    partial_fail = list(world.get("partial_fail_tools") or [])
    lights = None
    if world.get("ambiguous_lights"):
        lights = [
            {
                "entity_id": "light.a",
                "state": "off",
                "attributes": {"friendly_name": "Lampa A", "supported_color_modes": ["brightness"], "brightness": 100},
            },
            {
                "entity_id": "light.b",
                "state": "off",
                "attributes": {"friendly_name": "Lampa B", "supported_color_modes": ["brightness"], "brightness": 100},
            },
        ]
    elif world.get("unique_light"):
        lights = None  # harness default single light

    turn_traces: List[TurnTrace] = []
    turn_codes: List[List[str]] = []

    # Pick inject from first turn that declares it; may change mid-dialogue
    try:
        with jarvis_session(
            inject="ok",
            disabled_tools=disabled,
            partial_fail_tools=partial_fail,
            lights=lights,
        ) as (mgr, backend, _log):
            for spec in scenario.turns:
                if spec.inject:
                    backend.set_inject(spec.inject)
                elif scenario.turns and scenario.turns[0].inject and spec is scenario.turns[0]:
                    backend.set_inject(scenario.turns[0].inject)
                else:
                    # keep previous inject unless reset
                    pass
                before = len(backend.invoked)
                try:
                    result = mgr.handle(spec.user)
                except Exception as exc:
                    raw = {
                        "input": spec.user,
                        "response": f"ERROR: {exc}",
                        "source": "exception",
                        "tools": list(backend.invoked[before:]),
                        "observations": [],
                    }
                    tr = _to_turn_trace(raw)
                    turn_traces.append(tr)
                    turn_codes.append(["BLOCKED_EXCEPTION"])
                    continue
                raw = capture_trace(mgr, backend, user=spec.user, result=result, tools_before=before)
                tr = _to_turn_trace(raw)
                turn_traces.append(tr)
                codes, _ = evaluate_turn(spec, tr, scenario=scenario)
                turn_codes.append(codes)
    except Exception as exc:
        return ScenarioResult(
            scenario_id=scenario.id,
            seed=scenario.seed,
            category=scenario.category,
            score_bucket=scenario.score_bucket,
            verdict=Verdict.BLOCKED,
            expected_behavior=scenario.expected_behavior,
            actual_behavior=f"session_error: {exc}",
            failure_codes=["BLOCKED_SESSION"],
            root_cause_layer="harness",
            turns=[],
            tags=scenario.tags,
            critical=scenario.critical,
            false_success_safety=scenario.false_success_safety,
            poison_critical=scenario.poison_critical,
            deterministic_core=scenario.deterministic_core,
        )

    return evaluate_scenario(scenario, turn_traces, turn_codes)


def _serialize_result(r: ScenarioResult) -> Dict[str, Any]:
    return {
        "scenario_id": r.scenario_id,
        "seed": r.seed,
        "category": r.category,
        "score_bucket": r.score_bucket,
        "verdict": r.verdict.value,
        "expected_behavior": r.expected_behavior,
        "actual_behavior": r.actual_behavior,
        "failure_codes": list(r.failure_codes),
        "root_cause_layer": r.root_cause_layer,
        "critical": r.critical,
        "false_success_safety": r.false_success_safety,
        "poison_critical": r.poison_critical,
        "deterministic_core": r.deterministic_core,
        "known_exception": r.known_exception,
        "tags": list(r.tags),
        "turns": [asdict(t) for t in r.turns],
    }


def _write_artifacts(
    summary: GateSummary,
    results: Sequence[ScenarioResult],
    *,
    label: str,
    stats: Dict[str, Any],
) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ARTIFACT_DIR / f"{label}_{stamp}.json"
    payload = {
        "label": label,
        "generated_at": stamp,
        "stats": stats,
        "thresholds": THRESHOLDS,
        "summary": {
            "total": summary.total,
            "passed": summary.passed,
            "failed": summary.failed,
            "partial": summary.partial,
            "blocked": summary.blocked,
            "score": summary.score,
            "categories": summary.categories,
            "score_buckets": summary.score_buckets,
            "counters": summary.counters,
            "critical_pass_pct": summary.critical_pass_pct,
            "false_success_pass_pct": summary.false_success_pass_pct,
            "poison_critical_pass_pct": summary.poison_critical_pass_pct,
            "deterministic_core_pass_pct": summary.deterministic_core_pass_pct,
            "thresholds_met": summary.thresholds_met,
            "verdict": summary.verdict,
            "mode": summary.mode,
            "known_exceptions": summary.known_exceptions,
        },
        "failures": [_serialize_result(r) for r in summary.failures[:200]],
        "top_failures": [_serialize_result(r) for r in summary.failures[:30]],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # Also write a stable "latest" pointer for the label
    latest = ARTIFACT_DIR / f"{label}_latest.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def run_jarvis_behavioral(
    *,
    mode: Optional[str] = None,
    seed: Optional[int] = None,
    argv: Optional[Sequence[str]] = None,
    label: str = "run",
    limit: Optional[int] = None,
) -> int:
    """Run the gate. Returns process exit code (0 = thresholds met)."""
    import builtins
    import os

    mode = mode or _parse_mode(argv)
    seed = seed if seed is not None else _parse_seed(argv)
    if mode == "explore":
        return run_jarvis_explore(seed=seed, argv=argv, label=label if label != "run" else "explore")
    # Keep console readable; production code never reads this as test mode.
    os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")
    quiet = "--verbose" not in list(argv or sys.argv)

    _orig_print = builtins.print

    def _gate_print(*args, **kwargs):
        if not quiet:
            return _orig_print(*args, **kwargs)
        # Allow our own progress lines (they go through _orig_print directly).
        return None

    # Patch Diagnostics.log_turn noise
    try:
        import Diagnostics

        _orig_log = Diagnostics.log_turn

        def _quiet_log(*a, **k):
            if quiet:
                return None
            return _orig_log(*a, **k)

        Diagnostics.log_turn = _quiet_log  # type: ignore[assignment]
    except Exception:
        _orig_log = None

    _orig_print("=== ELIAN JARVIS BEHAVIORAL GATE ===")
    _orig_print(f"mode={mode} seed={seed}")

    scenarios = build_corpus(seed=seed, mode=mode)
    if limit is not None:
        scenarios = scenarios[: int(limit)]
    stats = corpus_stats(scenarios)
    _orig_print(
        f"corpus total={stats['total']} "
        f"single={stats['single_turn']} noisy={stats['noisy']} "
        f"multi={stats['multi_turn']} poison={stats['poison']} "
        f"regression={stats['regression']}"
    )
    _orig_print(
        f"flags critical={stats['critical']} "
        f"false_success={stats['false_success_safety']} "
        f"poison_critical={stats['poison_critical']} "
        f"det_core={stats['deterministic_core']}"
    )

    results: List[ScenarioResult] = []
    t0 = time.time()
    try:
        for i, sc in enumerate(scenarios, 1):
            try:
                results.append(run_scenario(sc))
            except Exception as exc:
                results.append(
                    ScenarioResult(
                        scenario_id=sc.id,
                        seed=sc.seed,
                        category=sc.category,
                        score_bucket=sc.score_bucket,
                        verdict=Verdict.BLOCKED,
                        expected_behavior=sc.expected_behavior,
                        actual_behavior=str(exc),
                        failure_codes=["BLOCKED_RUNNER"],
                        root_cause_layer="harness",
                        critical=sc.critical,
                        false_success_safety=sc.false_success_safety,
                        poison_critical=sc.poison_critical,
                        deterministic_core=sc.deterministic_core,
                    )
                )
            if i % 200 == 0 or i == len(scenarios):
                elapsed = time.time() - t0
                _orig_print(f"  progress {i}/{len(scenarios)} ({elapsed:.1f}s)")
    finally:
        if _orig_log is not None:
            try:
                import Diagnostics

                Diagnostics.log_turn = _orig_log  # type: ignore[assignment]
            except Exception:
                pass

    summary = aggregate(results, mode=mode)
    artifact = _write_artifacts(summary, results, label=label, stats=stats)
    summary.artifact_path = str(artifact)

    _orig_print()
    _orig_print(
        f"RESULT total={summary.total} "
        f"PASS={summary.passed} FAIL={summary.failed} "
        f"PARTIAL={summary.partial} BLOCKED={summary.blocked} "
        f"score={summary.score}%"
    )
    _orig_print(f"Counters: {summary.counters}")
    _orig_print(
        f"critical={summary.critical_pass_pct}% "
        f"false_success={summary.false_success_pass_pct}% "
        f"poison_critical={summary.poison_critical_pass_pct}% "
        f"det_core={summary.deterministic_core_pass_pct}%"
    )
    _orig_print(f"thresholds_met={summary.thresholds_met} verdict={summary.verdict}")
    _orig_print(f"artifact={artifact}")
    if summary.failures:
        _orig_print("TOP FAILURES:")
        for r in summary.failures[:15]:
            exp = (r.expected_behavior or "").encode("ascii", "replace").decode("ascii")
            _orig_print(
                f"  FAIL {r.scenario_id} codes={r.failure_codes} "
                f"layer={r.root_cause_layer} expected={exp!r}"
            )
    _orig_print("=== END JARVIS BEHAVIORAL GATE ===")
    return 0 if summary.thresholds_met else 1


def _acceptable_pct(results: Sequence[ScenarioResult]) -> float:
    if not results:
        return 100.0
    ok = sum(1 for r in results if r.verdict in (Verdict.PASS, Verdict.PARTIAL))
    return round(100.0 * ok / len(results), 3)


def run_jarvis_explore(
    *,
    seed: Optional[int] = None,
    argv: Optional[Sequence[str]] = None,
    label: str = "explore",
) -> int:
    """Fresh-validation pass: regression score vs novel/unseen utterances.

    Does not retune production for novel literals. Exit non-zero when
    regression looks strong but novel generalization is weak.
    """
    import os

    from core.jarvis_behavioral.corpus import build_explore_bundle

    seed = seed if seed is not None else _parse_seed(argv)
    novel_seed = seed if seed != 42 else 20260912
    os.environ.setdefault("ELIAN_PROTECT_USER_APPS", "1")

    print("=== ELIAN JARVIS EXPLORE (NOVEL VALIDATION) ===")
    print(f"novel_seed={novel_seed}")

    bundle = build_explore_bundle(behavioral_seed=42, novel_seed=novel_seed)
    overlap = bundle["overlap"]
    print(
        f"novel totals: single={bundle['novel_stats'].get('novel_single')} "
        f"multi={bundle['novel_stats'].get('novel_multi')} "
        f"poison={bundle['novel_stats'].get('novel_poison')} "
        f"compound={bundle['novel_stats'].get('novel_compound')} "
        f"overlap_with_dev={overlap.get('overlap_count')}"
    )
    if int(overlap.get("overlap_count") or 0) > 0:
        print(f"  WARNING overlap samples: {overlap.get('overlaps')[:5]}")

    # Regression: prefer last full gate artifact if present; else run critical+regression slice.
    reg_pct = None
    reg_source = "artifact"
    latest = ARTIFACT_DIR / "p2_final_latest.json"
    if not latest.exists():
        latest = ARTIFACT_DIR / "final_full5_latest.json"
    if latest.exists():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            reg_pct = float((payload.get("summary") or {}).get("score") or 0.0)
            print(f"REGRESSION PASS % (from {latest.name}): {reg_pct}")
        except Exception:
            reg_pct = None
    if reg_pct is None:
        reg_source = "live_critical"
        print("REGRESSION: running live critical corpus...")
        # Reuse behavioral runner critically
        run_jarvis_behavioral(mode="critical", seed=42, label="explore_regression_critical")
        crit_path = ARTIFACT_DIR / "explore_regression_critical_latest.json"
        payload = json.loads(crit_path.read_text(encoding="utf-8"))
        reg_pct = float((payload.get("summary") or {}).get("score") or 0.0)
        print(f"REGRESSION PASS % (live critical): {reg_pct}")

    novel_scenarios: List[Scenario] = list(bundle["novel"])
    print(f"Running novel scenarios: {len(novel_scenarios)}")
    results: List[ScenarioResult] = []
    t0 = time.time()
    # Quiet Diagnostics during novel run
    try:
        import Diagnostics

        _orig_log = Diagnostics.log_turn
        Diagnostics.log_turn = lambda *a, **k: None  # type: ignore[assignment]
    except Exception:
        _orig_log = None
    try:
        for i, sc in enumerate(novel_scenarios, 1):
            results.append(run_scenario(sc))
            if i % 100 == 0 or i == len(novel_scenarios):
                print(f"  novel progress {i}/{len(novel_scenarios)} ({time.time() - t0:.1f}s)")
    finally:
        if _orig_log is not None:
            try:
                import Diagnostics

                Diagnostics.log_turn = _orig_log  # type: ignore[assignment]
            except Exception:
                pass

    by_cat: Dict[str, List[ScenarioResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    novel_single_pct = _acceptable_pct(by_cat.get("novel_single") or [])
    novel_multi_pct = _acceptable_pct(by_cat.get("novel_multi") or [])
    novel_adv_pct = _acceptable_pct(by_cat.get("novel_poison") or [])
    novel_cmp_pct = _acceptable_pct(by_cat.get("novel_compound") or [])
    novel_overall = _acceptable_pct(results)

    print()
    print(f"REGRESSION PASS %: {reg_pct}  (source={reg_source})")
    print(f"NOVEL SINGLE-TURN %: {novel_single_pct}")
    print(f"NOVEL MULTI-TURN %: {novel_multi_pct}")
    print(f"NOVEL ADVERSARIAL %: {novel_adv_pct}")
    print(f"NOVEL COMPOUND %: {novel_cmp_pct}")
    print(f"NOVEL OVERALL %: {novel_overall}")

    # Generalization gate: strong regression + weak novel => not DONE
    novel_floor = 85.0
    category_ok = (
        novel_single_pct >= 90.0
        and novel_multi_pct >= 85.0
        and novel_adv_pct >= 90.0
        and novel_cmp_pct >= 85.0
    )
    generalizes = novel_overall >= novel_floor and category_ok
    if reg_pct >= 97.0 and not generalizes:
        explore_verdict = "FAILED_GENERALIZATION"
        generalizes_answer = (
            "NIE — regression silne, ale novel (zwłaszcza multi-turn/adversarial) poniżej progu; "
            "Elian nie generalizuje wystarczająco na niewidziane wcześniej wypowiedzi."
        )
        exit_code = 1
    elif generalizes and reg_pct >= 97.0:
        explore_verdict = "PASS_GENERALIZATION"
        generalizes_answer = (
            "TAK — Elian generalizuje na niewidziane wcześniej sformułowania "
            "(single/multi/adversarial/compound powyżej progów)."
        )
        exit_code = 0
    else:
        explore_verdict = "PARTIAL_GENERALIZATION"
        generalizes_answer = (
            "CZĘŚCIOWO — część novel kategorii przechodzi, ale nie wszystkie progi generalizacji."
        )
        exit_code = 1

    print(f"GENERALIZES?: {generalizes_answer}")
    print(f"explore_verdict={explore_verdict}")

    summary = aggregate(results, mode="explore")
    artifact = _write_artifacts(
        summary,
        results,
        label=label,
        stats={
            **bundle["novel_stats"],
            "regression_pass_pct": reg_pct,
            "novel_single_pct": novel_single_pct,
            "novel_multi_pct": novel_multi_pct,
            "novel_adversarial_pct": novel_adv_pct,
            "novel_compound_pct": novel_cmp_pct,
            "novel_overall_pct": novel_overall,
            "overlap": overlap,
            "explore_verdict": explore_verdict,
            "generalizes": generalizes_answer,
        },
    )
    print(f"artifact={artifact}")
    print("=== END JARVIS EXPLORE ===")
    return exit_code


if __name__ == "__main__":
    if "--jarvis-explore" in sys.argv or "--explore" in sys.argv:
        raise SystemExit(run_jarvis_explore())
    raise SystemExit(run_jarvis_behavioral())
