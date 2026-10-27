# ==========================
# ELIAN — performance diagnostics (Phase 9)
# ==========================

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from Config import DATA_DIR
from core.conversation_manager import ConversationManager
from core.metrics import get_metrics, reset_metrics
from core.model_client import FakeModelClient
from core.tools import ToolRegistry
from core.tool_result import ToolResult
from core.risk import RiskLevel
from core.tools import Tool
from core.version import version_banner
from memory.manager import MemoryManager


def _fake_registry():
    registry = ToolRegistry(confirm_medium=True)

    def volume_up(_arguments):
        return ToolResult.ok("Głośniej.", tool_name="volume_up")

    registry.register(
        Tool(
            name="volume_up",
            description="volume",
            handler=volume_up,
            risk_level=RiskLevel.SAFE,
        )
    )
    return registry


def _benchmark_fastpath() -> Dict[str, Any]:
    reset_metrics()
    with tempfile_dir():
        registry = _fake_registry()
        manager = ConversationManager(memory=[], registry=registry)
        manager.activate()
        t0 = time.perf_counter()
        with patch("core.conversation_manager.run_agent", return_value=None), patch(
            "core.conversation_manager.run_skills", return_value=None
        ), patch("core.conversation_manager.run_command", return_value=None), patch(
            "core.conversation_manager.run_intent", return_value=None
        ), patch("core.conversation_manager.ask_ai", return_value=None):
            result = manager.handle("głośniej")
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        snap = get_metrics().snapshot()
        return {
            "command": "głośniej",
            "source": result.source,
            "elapsed_ms": elapsed_ms,
            "agent_calls": snap["model_calls"]["agent"],
            "vision_calls": snap["model_calls"]["vision"],
            "legacy_fallback_used": snap["legacy_fallback_used"],
            "pass": (
                result.source in ("fast_path", "action_loop")
                and snap["model_calls"]["agent"] == 0
                and snap["model_calls"]["vision"] == 0
                and snap["legacy_fallback_used"] == 0
            ),
        }


def _benchmark_chat() -> Dict[str, Any]:
    reset_metrics()
    manager = ConversationManager(
        memory=[],
        registry=_fake_registry(),
        model=FakeModelClient([]),
    )
    manager.activate()
    with patch("core.conversation_manager.run_fast_path", return_value=None), patch(
        "core.conversation_manager.run_skills", return_value=None
    ), patch("core.conversation_manager.run_command", return_value=None), patch(
        "core.conversation_manager.run_intent", return_value=None
    ), patch("core.conversation_manager.run_agent", return_value=None), patch(
        "Brain.ask_ai", return_value="Bo rozprasza światło."
    ) as brain:
        result = manager.handle("Czemu niebo jest niebieskie?")
    return {
        "command": "Czemu niebo jest niebieskie?",
        "source": result.source,
        "brain_called": brain.called,
        "tool_calls": get_metrics().tool_calls,
        "pass": result.source == "chat" and get_metrics().tool_calls == 0,
    }


def _check_vision_lazy() -> Dict[str, Any]:
    from vision import provider as vp
    from vision.session import reset_vision_session_for_tests

    before = getattr(vp, "vision_model_init_calls", 0)
    reset_vision_session_for_tests(None)
    # Importing session module should not bump init counter
    from vision.session import VisionSession  # noqa: F401

    after_import = getattr(vp, "vision_model_init_calls", 0)
    return {
        "vision_model_init_calls_after_import": after_import - before,
        "pass": (after_import - before) == 0,
    }


def tempfile_dir():
    import tempfile

    class _Ctx:
        def __enter__(self):
            self._td = tempfile.TemporaryDirectory()
            return self._td

        def __exit__(self, *args):
            self._td.cleanup()

    return _Ctx()


def run_performance_diagnostics(*, save: bool = True) -> int:
    print("=== ELIAN PERFORMANCE DIAGNOSTICS ===")
    print(version_banner())
    print("(deterministic offline checks — no mic / no real GPU latency claims)")
    print()

    failed = 0
    results: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "benchmarks": {},
    }

    for name, fn in (
        ("fastpath_volume", _benchmark_fastpath),
        ("chat_brain", _benchmark_chat),
        ("vision_lazy_init", _check_vision_lazy),
    ):
        try:
            row = fn()
        except Exception as exc:
            row = {"pass": False, "error": str(exc)}
        results["benchmarks"][name] = row
        status = "PASS" if row.get("pass") else "FAIL"
        print(f"Check: {name}  {status}")
        if not row.get("pass"):
            failed += 1
        for key, value in row.items():
            if key != "pass":
                print(f"  {key}: {value}")
        print()

    if save:
        out_dir = Path(DATA_DIR) / "diagnostics"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "performance_latest.json"
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved: {out_path}")
        print()

    print("=== END PERFORMANCE DIAGNOSTICS ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_performance_diagnostics())
