# ==========================
# ELIAN — C.5 screen-perception smoke (no mic, no disk frames)
# ==========================
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_screen_perception_smoke() -> int:
    from core.computer_agent import (
        ComputerAgent,
        computer_quality_metrics,
        notepad_fixture_store,
        reset_computer_agent_for_tests,
        reset_computer_metrics,
    )
    from core.perception.frame_provider import MemoryFrameProvider
    from core.perception.metrics import reset_perception_metrics
    from core.perception.screen_runtime import ScreenPerceptionRuntime, reset_screen_runtime_for_tests
    from core.perception.visual_referent import VisualReferent
    from core.perception.vision_router import LocalVisionProvider, VisionRouter
    from core.world_state import get_world_state, reset_world_state
    from vision.uia import FakeUIBackend

    reset_computer_metrics()
    reset_perception_metrics()
    reset_world_state()

    provider = MemoryFrameProvider()
    vision = VisionRouter(
        provider=LocalVisionProvider(
            mock={
                "description": "menu",
                "candidates": [
                    {"label": "trzy kropki", "role": "button", "bbox": [10, 10, 40, 40], "confidence": 0.94}
                ],
            }
        )
    )
    runtime = ScreenPerceptionRuntime(provider=provider, vision=vision)
    reset_screen_runtime_for_tests(runtime)
    rows = []

    def check(name, ok, detail=""):
        rows.append((name, bool(ok), str(detail or "")))

    runtime.start()
    check("capture_runtime", runtime.health().get("started") is True)

    frame = provider.push(b"frame-a", width=64, height=64, hwnd=1)
    runtime.ingest_frame(frame, hwnd=1, title="Notatnik")
    check("frame_update", runtime.get_latest_frame() is not None and runtime.get_state().frame_id == frame.frame_id)

    calls_before = vision.provider.calls
    runtime.ingest_frame(provider.push(b"frame-a", width=64, height=64, hwnd=1), hwnd=1, title="Notatnik")
    check("change_detection", vision.provider.calls == calls_before)

    frame_b = provider.push(b"frame-b-major", width=64, height=64, hwnd=2)
    change = runtime.ingest_frame(frame_b, hwnd=2, title="Kalkulator")
    check("uia_fusion", change.window_changed or change.major or runtime.get_state().active_window.get("hwnd") == 2)

    vision_off = VisionRouter(provider=LocalVisionProvider())
    vision_off.enabled = False
    runtime.vision = vision_off
    fallback = runtime.request_visual_understanding("co to", reason="test")
    check("vision_disabled_fallback", fallback is not None)

    runtime.vision = vision
    loc = runtime.request_visual_understanding("gdzie jest menu", require_localization=True, reason="test")
    check(
        "vision_mock_localization",
        bool((loc.get("candidates") or [{}])[0].get("bbox")) and float((loc.get("candidates") or [{}])[0].get("confidence") or 0) >= 0.9,
    )

    fresh = runtime.assert_frame_fresh(runtime.get_state().frame_id, hwnd=2)
    stale = runtime.assert_frame_fresh(1, hwnd=2)
    check("stale_frame_guard", fresh and not stale)

    amb = runtime.resolve_visual_referent(
        "kliknij to",
        candidates=[
            VisualReferent(label="a", confidence=0.83, bbox=[0, 0, 10, 10]),
            VisualReferent(label="b", confidence=0.82, bbox=[0, 20, 10, 30]),
        ],
    )
    check("ambiguity_guard", amb.get("clarify") is True and not amb.get("ok"))

    runtime.fuse_ui(
        [{"element_id": "pw", "role": "password", "is_password": True, "value": "secret-pass"}]
    )
    public = runtime.get_state().to_public_dict()
    world = get_world_state().get("screen") or {}
    leaked = "secret-pass" in str(public) or "secret-pass" in str(world)
    check("sensitive_redaction", (not leaked) and runtime.get_state().sensitive_region)

    check("no_disk_storage", runtime.disk_writes == 0 and runtime.provider.saved_to_disk() is False)

    from core.perception.foreground import FakeForegroundProvider
    from core.perception.screen_truth import answer_screen_query

    if not isinstance(runtime.foreground, FakeForegroundProvider):
        runtime.foreground = FakeForegroundProvider()
    runtime.observe_window_meta(hwnd=1, title="Notatnik", process="notepad.exe")
    runtime.foreground.set(hwnd=88, title="ChatGPT - Google Chrome", process="chrome.exe")
    spoken, obs = answer_screen_query("co mam na ekranie?")
    check(
        "fresh_overrides_notepad",
        "chatgpt" in spoken.lower() and "notatnik" not in spoken.lower() and bool(obs.refreshed),
        spoken,
    )

    store = notepad_fixture_store()
    agent = ComputerAgent(store)
    agent._fake_ui = FakeUIBackend()
    reset_computer_agent_for_tests(agent)
    before = agent.vision_requests
    agent.act("type_text", {"text": "Elian działa jak agent", "app_name": "Notatnik"})
    agent.act("select_all", {"semantic_action": "select_all", "app_name": "Notatnik"})
    check(
        "computeragent_integration",
        agent.vision_requests == before,
        f"vision_requests={agent.vision_requests}",
    )
    check("performance_no_unneeded_vision", agent.vision_requests == 0)

    runtime.stop()
    check("capture_stop", runtime.health().get("started") is False or runtime.get_state().perception_health == "stopped")

    for name, ok, detail in rows:
        print(f"[PERCEPTION-SMOKE] {'PASS' if ok else 'FAIL'} {name}: {detail}")

    metrics = computer_quality_metrics()
    bad = {k: v for k, v in metrics.items() if int(v or 0) != 0}
    # ambiguity_guard increments ambiguous_visual_action by design
    ignorable = {"ambiguous_visual_action", "stale_visual_action", "vision_requests"}
    leftover = {k: v for k, v in bad.items() if k not in ignorable}
    print(f"[METRICS] {metrics}")
    passed = sum(1 for _n, ok, _d in rows if ok)
    print(f"screen-perception-smoke {passed}/{len(rows)}")
    if passed == len(rows) and not leftover:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(run_screen_perception_smoke())
