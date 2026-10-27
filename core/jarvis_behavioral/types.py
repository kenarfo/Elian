# ==========================
# ELIAN — Jarvis Behavioral Gate types (P2)
# ==========================
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


class ExpectMode(str, Enum):
    ACT = "ACT"
    TALK = "TALK"
    ASK = "ASK"
    INFO = "INFO"
    NO_ACTION = "NO_ACTION"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    HONEST_FAILURE = "HONEST_FAILURE"
    CANCEL = "CANCEL"
    RETRY = "RETRY"
    REPLAN = "REPLAN"


# Side-effect tool families used by evaluators (harness-side only).
SIDE_EFFECT_TOOLS = frozenset(
    {
        "open_app",
        "close_app",
        "play_media",
        "pause_media",
        "next_track",
        "previous_track",
        "volume_up",
        "volume_down",
        "set_volume",
        "turn_on_device",
        "turn_off_device",
        "set_device_value",
        "web_search",
        "open_website",
        "browser_navigate",
        "browser_click",
        "create_reminder",
        "cancel_reminder",
        "create_calendar_event",
        "type_text",
        "click",
        "ui_click",
        "set_preference",
        "forget_preference",
        "save_memory",
        "forget_memory",
    }
)

SUCCESS_CLAIM_MARKERS = (
    "gotowe",
    "zrobione",
    "już jest",
    "juz jest",
    "włączone",
    "wlaczone",
    "wyłączone",
    "wylaczone",
    "otworzyłem",
    "otworzylem",
    "uruchomiłem",
    "uruchomilem",
)

# Positive success claims only when not negated.
_SUCCESS_RE = (
    r"(?<!nie\s)(?<!nie\sudało\s)"  # soft guard; primary filter in helper
)


@dataclass
class TurnSpec:
    """One user turn inside a scenario."""

    user: str
    expect_mode: Optional[ExpectMode] = None
    expect_tool_any: Tuple[str, ...] = ()
    forbid_tools: Tuple[str, ...] = ()
    expect_ask: Optional[bool] = None
    expect_no_side_effect: Optional[bool] = None
    inject: str = ""  # fail|timeout|partial|verify_fail|offline|no_change
    notes: str = ""


@dataclass
class Scenario:
    """Corpus scenario — expectations live only in the gate, never in production."""

    id: str
    category: str
    turns: List[TurnSpec]
    score_bucket: str = "ACTION_REQUEST"
    tags: Tuple[str, ...] = ()
    seed: int = 0
    domain: str = ""  # HOME/MEDIA/...
    critical: bool = False
    false_success_safety: bool = False
    poison_critical: bool = False
    deterministic_core: bool = False
    world: Dict[str, Any] = field(default_factory=dict)
    expected_behavior: str = ""
    prior_bug: str = ""


@dataclass
class TurnTrace:
    input: str
    response: str = ""
    source: str = ""
    semantic_mode: str = ""
    semantic_intent: str = ""
    utterance_class: str = ""
    active_goal: str = ""
    discourse: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    worldstate: Dict[str, Any] = field(default_factory=dict)
    memory_keys: List[str] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    plan: str = ""
    verify: str = ""
    repair: str = ""


@dataclass
class ScenarioResult:
    scenario_id: str
    seed: int
    category: str
    score_bucket: str
    verdict: Verdict
    expected_behavior: str
    actual_behavior: str
    failure_codes: List[str] = field(default_factory=list)
    root_cause_layer: str = ""
    turns: List[TurnTrace] = field(default_factory=list)
    tags: Tuple[str, ...] = ()
    critical: bool = False
    false_success_safety: bool = False
    poison_critical: bool = False
    deterministic_core: bool = False
    known_exception: str = ""


@dataclass
class GateSummary:
    total: int = 0
    passed: int = 0
    failed: int = 0
    partial: int = 0
    blocked: int = 0
    score: float = 0.0
    categories: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    score_buckets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    counters: Dict[str, int] = field(default_factory=dict)
    critical_pass_pct: float = 0.0
    false_success_pass_pct: float = 0.0
    poison_critical_pass_pct: float = 0.0
    deterministic_core_pass_pct: float = 0.0
    thresholds_met: bool = False
    verdict: str = "FAILED"
    failures: List[ScenarioResult] = field(default_factory=list)
    known_exceptions: List[Dict[str, str]] = field(default_factory=list)
    mode: str = "full"
    artifact_path: str = ""
