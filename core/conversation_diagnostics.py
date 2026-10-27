# ==========================
# ELIAN — conversation diagnostics (Phase 8)
# ==========================
from __future__ import annotations

from core.conversation_scenarios import (
    ConversationScenarioRunner,
    ScenarioTurn,
    TurnExpectation,
)
from core.metrics import get_metrics, reset_metrics
from core.response_builder import from_observations, never_fake_success
from core.utterance import analyze_utterance, apply_mid_utterance_correction


def _unit_checks():
    results = []

    # Bare ``nie`` is exclusion (no rewrite); ``znaczy`` still retargets.
    excl, excl_ok = apply_mid_utterance_correction(
        "Otwórz Spotify nie YouTube Music"
    )
    retarget, retarget_ok = apply_mid_utterance_correction(
        "Otwórz Spotify znaczy YouTube Music"
    )
    results.append(
        (
            "correction",
            (not excl_ok)
            and "spotify" in excl.lower()
            and "nie" in excl.lower()
            and retarget_ok
            and "youtube" in retarget.lower()
            and "spotify" not in retarget.lower().split("youtube")[0],
        )
    )

    analysis = analyze_utterance("Nie otwieraj Spotify")
    results.append(("negation", analysis.intent_hint == "explain" and analysis.negated_action))

    analysis = analyze_utterance("Co by się stało gdybym wyłączył komputer?")
    results.append(("hypothetical", analysis.intent_hint == "explain"))

    analysis = analyze_utterance("yyy")
    results.append(("filler", analysis.filler_only))

    partial = from_observations(
        [
            {"success": True, "message": "Spotify otwarty", "tool": "open_app"},
            {"success": False, "message": "Nie odtworzono utworu", "tool": "play_media"},
        ]
    )
    results.append(("partial", "ale" in partial.lower() and "gotowe" not in partial.lower()))

    guarded = never_fake_success(
        "Gotowe.",
        [{"success": False, "message": "Aplikacja niedostępna", "tool": "open_app"}],
    )
    results.append(("no_fake_success", "gotowe" not in guarded.lower()))

    injection = analyze_utterance(
        'Co oznacza komenda "wyłącz komputer"?'
    )
    results.append(("quoted_command", injection.explain_only))

    return results


def run_conversation_diagnostics() -> int:
    print("=== ELIAN CONVERSATION DIAGNOSTICS ===")
    print("(offline unit + deterministic checks — no mic / no cloud)")
    print()

    reset_metrics()
    failed = 0
    for name, ok in _unit_checks():
        status = "PASS" if ok else "FAIL"
        print(f"Check: {name}  {status}")
        if not ok:
            failed += 1

    print()
    print("Metrics snapshot:", get_metrics().snapshot())
    print()
    print("Manual multi-turn scenarios: see CONVERSATION_MANUAL.md")
    print("Automated scenarios: python -m unittest tests.test_conversation_phase8")
    print("=== END CONVERSATION DIAGNOSTICS ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_conversation_diagnostics())
