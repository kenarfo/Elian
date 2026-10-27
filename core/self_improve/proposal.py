# ==========================
# ELIAN — Self-Improvement / Proposal Generator
# ==========================
"""Gap → technical requirement → concrete code the assistant wants to adopt.

Templates are deterministic and dependency-free: every generated module is
pure Python (sandbox import allowlist only), exposes ``TOOL_SPEC`` + ``handle()``
so it can be hot-swapped into the ToolRegistry, and ships with its own
unittest file that the Execution Sandbox runs before adoption.

Nothing here touches core logic — the generated code only lands in
``core/self_improve/learned/`` after Verification + human approval.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from core.persistence import safe_read_json
from core.self_improve.experience_log import utc_now_iso
from core.self_improve.gap_analysis import CapabilityGap, GapAnalyzer, _slug

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "Data" / "self_improve"
DEFAULT_PATH = DATA_DIR / "proposals.json"
SCHEMA_VERSION = 1


class ProposalStatus(str, Enum):
    DRAFT = "DRAFT"                            # generated, not yet verified
    VERIFIED = "VERIFIED"                      # sandbox + verification passed
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    APPROVED = "APPROVED"                      # human said yes
    REJECTED = "REJECTED"                      # human said no
    ADOPTED = "ADOPTED"                        # installed into learned/ + registry


@dataclass
class Requirement:
    """The technical requirement the proposal satisfies."""

    id: str
    title: str
    statement: str
    acceptance: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "statement": self.statement,
            "acceptance": list(self.acceptance),
        }

    @classmethod
    def from_dict(cls, row: Any) -> "Requirement":
        row = dict(row or {})
        return cls(
            id=str(row.get("id") or ""),
            title=str(row.get("title") or ""),
            statement=str(row.get("statement") or ""),
            acceptance=[str(item) for item in row.get("acceptance") or []],
        )


@dataclass
class GeneratedArtifact:
    module_name: str
    tool_name: str
    code: str
    tests: str
    test_module: str
    sample_args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    id: str
    gap_id: str
    gap_name: str
    domain: str
    kind: str
    requirement: Requirement
    module_name: str
    tool_name: str
    code: str
    tests: str
    test_module: str
    sample_args: Dict[str, Any] = field(default_factory=dict)
    status: str = ProposalStatus.DRAFT.value
    created_at: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    verification: Optional[Dict[str, Any]] = None
    approval: Optional[Dict[str, Any]] = None
    adoption: Optional[Dict[str, Any]] = None

    @property
    def status_enum(self) -> ProposalStatus:
        try:
            return ProposalStatus(self.status)
        except ValueError:
            return ProposalStatus.DRAFT

    @property
    def target_path(self) -> Path:
        """Where the module lands once adopted (additive — never a core file)."""
        return PROJECT_ROOT / "core" / "self_improve" / "learned" / f"{self.module_name}.py"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "gap_id": self.gap_id,
            "gap_name": self.gap_name,
            "domain": self.domain,
            "kind": self.kind,
            "requirement": self.requirement.to_dict(),
            "module_name": self.module_name,
            "tool_name": self.tool_name,
            "code": self.code,
            "tests": self.tests,
            "test_module": self.test_module,
            "sample_args": dict(self.sample_args),
            "status": self.status,
            "created_at": self.created_at,
            "evidence_ids": list(self.evidence_ids),
            "verification": self.verification,
            "approval": self.approval,
            "adoption": self.adoption,
        }

    @classmethod
    def from_dict(cls, row: Any) -> Optional["Proposal"]:
        if not isinstance(row, dict) or not row.get("id"):
            return None
        return cls(
            id=str(row["id"]),
            gap_id=str(row.get("gap_id") or ""),
            gap_name=str(row.get("gap_name") or ""),
            domain=str(row.get("domain") or "unknown"),
            kind=str(row.get("kind") or "general"),
            requirement=Requirement.from_dict(row.get("requirement")),
            module_name=str(row.get("module_name") or ""),
            tool_name=str(row.get("tool_name") or ""),
            code=str(row.get("code") or ""),
            tests=str(row.get("tests") or ""),
            test_module=str(row.get("test_module") or ""),
            sample_args=dict(row.get("sample_args") or {}),
            status=str(row.get("status") or ProposalStatus.DRAFT.value),
            created_at=str(row.get("created_at") or ""),
            evidence_ids=[str(item) for item in row.get("evidence_ids") or []],
            verification=row.get("verification"),
            approval=row.get("approval"),
            adoption=row.get("adoption"),
        )


class ProposalStore:
    """JSON persistence for proposals (code included)."""

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> Dict[str, Proposal]:
        raw, _ = safe_read_json(self.path, default={"proposals": {}})
        rows = raw.get("proposals") if isinstance(raw, dict) else raw
        proposals: Dict[str, Proposal] = {}
        if isinstance(rows, dict):
            items = rows.values()
        else:
            items = rows or []
        for row in items:
            proposal = Proposal.from_dict(row)
            if proposal is not None:
                proposals[proposal.id] = proposal
        return proposals

    def save(self, proposals: Dict[str, Proposal]) -> None:
        payload = {
            "schema": SCHEMA_VERSION,
            "updated_at": utc_now_iso(),
            "proposals": {pid: proposal.to_dict() for pid, proposal in proposals.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def get(self, proposal_id: str) -> Proposal:
        proposals = self.load()
        proposal = proposals.get(str(proposal_id or ""))
        if proposal is None:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        return proposal

    def put(self, proposal: Proposal) -> Proposal:
        proposals = self.load()
        proposals[proposal.id] = proposal
        self.save(proposals)
        return proposal

    def list(self, status: Optional[str] = None) -> List[Proposal]:
        proposals = list(self.load().values())
        if status:
            wanted = str(status).upper()
            proposals = [item for item in proposals if item.status == wanted]
        proposals.sort(key=lambda item: item.created_at)
        return proposals


# =========================================================================
# Code templates
# =========================================================================

def _template_scheduling(gap: CapabilityGap) -> GeneratedArtifact:
    module_name = "recurring_schedule"
    code = '''
"""Recurring schedule helper (generated by Elian ProposalGenerator).

Pure functions only: no I/O, no network, imports from the sandbox allowlist.
Covers the rules the assistant kept failing on: "every other Friday",
"each Monday", "last day of the month".
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

WEEKDAYS = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

#: Full Polish day names. User-facing strings must never leak the short codes.
WEEKDAY_LABELS_PL = ["poniedziałek", "wtorek", "środa", "czwartek",
                     "piątek", "sobota", "niedziela"]


def parse_date(value):
    """ISO string (YYYY-MM-DD) or date -> date."""
    if isinstance(value, date):
        return value
    text = str(value if value is not None else "").strip()
    if not text:
        raise ValueError("missing date")
    return date.fromisoformat(text)


def _weekday_index(value):
    if isinstance(value, bool):
        raise ValueError("weekday must be a name or 0-6")
    if isinstance(value, int):
        if 0 <= value <= 6:
            return value
        raise ValueError("weekday out of range: %r" % (value,))
    key = str(value if value is not None else "").strip().lower()
    if key in WEEKDAYS:
        return WEEKDAYS[key]
    raise ValueError("unknown weekday: %r" % (value,))


def _add_months(day, months):
    total = day.month - 1 + int(months)
    year = day.year + total // 12
    month = total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


#: Safety valve for `from_date` far in the future, so `next` always terminates.
_MAX_SCAN = 10000


def next_occurrence(start=None, weekday=None, from_date=None, interval=1,
                    freq="weekly", byweekday=None):
    """First occurrence on/after `from_date` for a rule.

    Speaks the same vocabulary as :func:`expand_occurrences` (`freq`,
    `byweekday`, `interval`); `weekday` stays as a singular alias.
    """
    start_date = parse_date(start)
    offsets = _parse_byweekday(byweekday) or _parse_byweekday(weekday)
    stream = _rule_stream(start_date, freq, interval, offsets)
    cursor = parse_date(from_date) if from_date else None
    for index, day in enumerate(stream):
        if cursor is None or day >= cursor:
            return day
        if index > _MAX_SCAN:
            break
    raise ValueError("no occurrence within %d steps" % _MAX_SCAN)


def _parse_byweekday(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [part for part in value.replace(",", " ").split() if part]
    else:
        parts = list(value)
    return sorted({_weekday_index(part) for part in parts})


def _iter_weekly(start, step_weeks, offsets):
    """Yield real occurrences of `offsets` (calendar weekday numbers).

    `offsets` are weekday indices (0 = Monday), so anchor on the Monday of
    `start`'s week. Adding a weekday index to an arbitrary `start` silently
    yields the wrong weekday — e.g. start=Saturday, byweekday=["fri"] would
    return Wednesdays.
    """
    week = start - timedelta(days=start.weekday())
    while True:
        for offset in offsets:
            day = week + timedelta(days=offset)
            if day >= start:
                yield day
        week += timedelta(days=7 * step_weeks)


def _iter_daily(start, step_days):
    cursor = start
    while True:
        yield cursor
        cursor += timedelta(days=step_days)


def _iter_monthly(start, step_months):
    # Anchor on the original day-of-month so 31 Jan -> 28 Feb -> 31 Mar,
    # not 31 Jan -> 28 Feb -> 28 Mar.
    months = 0
    while True:
        yield _add_months(start, months)
        months += step_months


def _rule_stream(start_date, freq="weekly", interval=1, offsets=None):
    """The one occurrence stream shared by `expand` and `next`.

    Both actions go through here so they can never drift apart again.
    """
    step = int(interval)
    if step < 1:
        raise ValueError("interval must be >= 1")
    key = str(freq if freq is not None else "weekly").strip().lower()
    if key in ("weekly", "week"):
        return _iter_weekly(start_date, step, offsets or [start_date.weekday()])
    if key in ("daily", "day", "days"):
        return _iter_daily(start_date, step)
    if key in ("monthly", "month"):
        return _iter_monthly(start_date, step)
    raise ValueError("unknown frequency: %r" % (freq,))


def expand_occurrences(start, count, freq="weekly", interval=1, byweekday=None, until=None):
    """Dates for a rule; stops at `count` or at `until` (inclusive)."""
    total = int(count)
    if total < 1:
        raise ValueError("count must be >= 1")
    start_date = parse_date(start)
    until_date = parse_date(until) if until else None
    stream = _rule_stream(start_date, freq, interval, _parse_byweekday(byweekday))
    out = []
    for day in stream:
        if until_date is not None and day > until_date:
            break
        out.append(day)
        if len(out) >= total:
            break
    return out


def describe_recurrence(freq="weekly", interval=1, byweekday=None):
    """Polished Polish phrasing for a rule, e.g. 'co 2 tygodnie w piątek'."""
    key = str(freq if freq is not None else "weekly").strip().lower()
    step = int(interval)
    if step < 1:
        raise ValueError("interval must be >= 1")
    if key in ("daily", "day", "days"):
        return "codziennie" if step == 1 else "co %d dni" % step
    if key in ("weekly", "week"):
        base = "co tydzień" if step == 1 else "co %d tygodnie" % step
        offsets = _parse_byweekday(byweekday)
        if offsets:
            return base + " w " + ", ".join(WEEKDAY_LABELS_PL[index] for index in offsets)
        return base
    if key in ("monthly", "month"):
        return "co miesiąc" if step == 1 else "co %d miesięcy" % step
    raise ValueError("unknown frequency: %r" % (freq,))


def _resolve_offsets(arguments):
    """One vocabulary for every action: `byweekday` array, `weekday` as alias."""
    args = dict(arguments or {})
    raw = args.get("byweekday")
    if raw is None or raw == "" or raw == []:
        raw = args.get("weekday")
    return _parse_byweekday(raw)


TOOL_SPEC = {
    "name": "recurring_schedule",
    "description": "Expand and query recurring schedules (daily/weekly/monthly rules) without a calendar backend.",
    "risk_level": "safe",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["next", "expand", "describe"]},
            "start": {"type": "string", "description": "ISO start date (YYYY-MM-DD)"},
            "from_date": {"type": "string",
                          "description": "ISO date; for 'next' return the first occurrence on/after it"},
            "count": {"type": "integer", "description": "how many dates 'expand' should return"},
            "freq": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
            "interval": {"type": "integer",
                         "description": "repeat every N units; 2 with weekly = 'every other week'"},
            "byweekday": {"type": "array", "items": {"type": "string"},
                          "description": "day names, e.g. ['mon','fri']"},
            "until": {"type": "string", "description": "ISO date; 'expand' stops on or after it"},
        },
        "required": ["action"],
    },
}


def handle(arguments):
    """Tool contract: ({}, ...) -> {success, message, data}."""
    args = dict(arguments or {})
    action = str(args.get("action") or "").strip().lower()
    try:
        if action == "next":
            day = next_occurrence(
                args.get("start"),
                from_date=args.get("from_date"),
                interval=int(args.get("interval") or 1),
                freq=args.get("freq") or "weekly",
                byweekday=_resolve_offsets(args),
            )
            return {"success": True, "message": "Następny termin: " + day.isoformat(),
                    "data": {"occurrence": day.isoformat()}}
        if action == "expand":
            days = expand_occurrences(
                args.get("start"),
                int(args.get("count") or 3),
                freq=args.get("freq") or "weekly",
                interval=int(args.get("interval") or 1),
                byweekday=_resolve_offsets(args),
                until=args.get("until"),
            )
            iso = [day.isoformat() for day in days]
            return {"success": True, "message": "Znaleziono %d terminów." % len(iso),
                    "data": {"occurrences": iso}}
        if action == "describe":
            text = describe_recurrence(
                args.get("freq") or "weekly",
                int(args.get("interval") or 1),
                _resolve_offsets(args),
            )
            return {"success": True, "message": text, "data": {"text": text}}
        return {"success": False,
                "message": "Unknown action: %r" % (action or "<missing>"),
                "data": {"action": action}}
    except (TypeError, ValueError, KeyError) as exc:
        return {"success": False, "message": "Invalid schedule input: %s" % exc,
                "data": {"error": str(exc)}}
'''
    tests = '''
"""Tests for recurring_schedule (generated by ProposalGenerator).

Deliberately starts on a NON-Monday date in several places: anchoring weekday
math on the start date instead of the real calendar silently returns the wrong
weekday, and a suite that only ever starts on a Monday cannot see it.
"""
from __future__ import annotations

import unittest

import recurring_schedule as rs


class NextOccurrenceTests(unittest.TestCase):
    def test_same_week_target(self):
        self.assertEqual(rs.next_occurrence("2026-01-05", "friday").isoformat(), "2026-01-09")

    def test_short_weekday_alias(self):
        self.assertEqual(rs.next_occurrence("2026-01-05", "fri").isoformat(), "2026-01-09")

    def test_interval_two_weeks_from_later_date(self):
        got = rs.next_occurrence("2026-01-05", "fri", from_date="2026-01-12", interval=2)
        self.assertEqual(got.isoformat(), "2026-01-23")

    def test_missing_start_raises(self):
        with self.assertRaises(ValueError):
            rs.next_occurrence("", "mon")

    def test_next_accepts_the_expand_vocabulary(self):
        got = rs.next_occurrence("2026-01-07", freq="weekly", byweekday=["fri"])
        self.assertEqual(got.isoformat(), "2026-01-09")

    def test_next_from_non_monday_start(self):
        got = rs.next_occurrence("2026-01-10", freq="weekly", byweekday=["fri"])
        self.assertEqual(got.isoformat(), "2026-01-16")


class ExpandTests(unittest.TestCase):
    def test_weekly_three(self):
        days = rs.expand_occurrences("2026-01-05", 3)
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-01-05", "2026-01-12", "2026-01-19"])

    def test_weekly_two_weekdays(self):
        days = rs.expand_occurrences("2026-01-05", 4, byweekday=["mon", "wed"])
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-01-05", "2026-01-07", "2026-01-12", "2026-01-14"])

    def test_weekday_is_real_from_a_saturday_start(self):
        days = rs.expand_occurrences("2026-01-10", 3, byweekday=["fri"])
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-01-16", "2026-01-23", "2026-01-30"])
        for day in days:
            self.assertEqual(day.weekday(), 4, day)

    def test_weekday_is_real_from_a_thursday_start(self):
        days = rs.expand_occurrences("2026-01-08", 2, byweekday=["mon"])
        self.assertEqual([d.isoformat() for d in days], ["2026-01-12", "2026-01-19"])

    def test_every_other_friday_from_a_wednesday(self):
        days = rs.expand_occurrences("2026-01-07", 3, freq="weekly",
                                     interval=2, byweekday=["fri"])
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-01-09", "2026-01-23", "2026-02-06"])
        for day in days:
            self.assertEqual(day.weekday(), 4, day)

    def test_daily_with_interval(self):
        days = rs.expand_occurrences("2026-01-05", 3, freq="daily", interval=2)
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-01-05", "2026-01-07", "2026-01-09"])

    def test_monthly_clamps_short_months(self):
        days = rs.expand_occurrences("2026-01-31", 3, freq="monthly")
        self.assertEqual([d.isoformat() for d in days],
                         ["2026-01-31", "2026-02-28", "2026-03-31"])

    def test_until_stops_early(self):
        days = rs.expand_occurrences("2026-01-05", 10, until="2026-01-13")
        self.assertEqual([d.isoformat() for d in days], ["2026-01-05", "2026-01-12"])

    def test_unknown_frequency_raises(self):
        with self.assertRaises(ValueError):
            rs.expand_occurrences("2026-01-05", 2, freq="hourly")


class DescribeTests(unittest.TestCase):
    def test_weekly_with_days_is_polished(self):
        self.assertEqual(rs.describe_recurrence("weekly", 2, ["fri"]),
                         "co 2 tygodnie w piątek")

    def test_multiple_days_are_named_in_full(self):
        self.assertEqual(rs.describe_recurrence("weekly", 1, ["mon", "wed"]),
                         "co tydzień w poniedziałek, środa")

    def test_daily(self):
        self.assertEqual(rs.describe_recurrence("daily", 1), "codziennie")

    def test_monthly(self):
        self.assertEqual(rs.describe_recurrence("monthly", 3), "co 3 miesięcy")


class HandleTests(unittest.TestCase):
    def test_expand_action(self):
        out = rs.handle({"action": "expand", "start": "2026-01-05", "count": 2})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["occurrences"], ["2026-01-05", "2026-01-12"])

    def test_next_action_with_legacy_weekday(self):
        out = rs.handle({"action": "next", "start": "2026-01-05", "weekday": "mon"})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["occurrence"], "2026-01-05")

    def test_next_action_with_byweekday(self):
        out = rs.handle({"action": "next", "start": "2026-01-07", "byweekday": ["fri"]})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["occurrence"], "2026-01-09")

    def test_every_action_shares_one_vocabulary(self):
        args = {"start": "2026-01-07", "freq": "weekly", "byweekday": ["fri"],
                "interval": 1, "count": 2}
        for action in rs.TOOL_SPEC["parameters"]["properties"]["action"]["enum"]:
            out = rs.handle(dict(args, action=action))
            self.assertTrue(out["success"], "%s failed: %s" % (action, out["message"]))

    def test_user_facing_messages_never_leak_short_weekday_codes(self):
        out = rs.handle({"action": "describe", "freq": "weekly", "byweekday": ["fri"]})
        self.assertNotIn("fri", out["message"].lower())

    def test_missing_action_fails_gracefully(self):
        out = rs.handle({})
        self.assertFalse(out["success"])
        self.assertTrue(out["message"])

    def test_bad_date_fails_gracefully(self):
        out = rs.handle({"action": "expand", "start": "not-a-date"})
        self.assertFalse(out["success"])

    def test_tool_spec_shape(self):
        self.assertEqual(rs.TOOL_SPEC["name"], "recurring_schedule")
        self.assertIn("action", rs.TOOL_SPEC["parameters"]["properties"])
        self.assertEqual(rs.TOOL_SPEC["parameters"]["required"], ["action"])

    def test_tool_spec_has_one_name_per_concept(self):
        props = rs.TOOL_SPEC["parameters"]["properties"]
        self.assertNotIn("weekday", props)


if __name__ == "__main__":
    unittest.main()
'''
    return GeneratedArtifact(
        module_name=module_name,
        tool_name=module_name,
        code=code.strip() + "\n",
        tests=tests.strip() + "\n",
        test_module=f"test_{module_name}",
        sample_args={"action": "expand", "start": "2026-01-05", "count": 3},
    )


def _template_tool_lookup(gap: CapabilityGap) -> GeneratedArtifact:
    module_name = "tool_naturalizer"
    code = '''
"""Map free-form phrasing to an existing tool name (generated).

Deterministic token-overlap scoring: no model, no network. Used when the
Experience Log keeps showing NO_TOOL_MATCH / WRONG_TOOL for a domain.
"""
from __future__ import annotations

import re

STOPWORDS = frozenset({
    "a", "an", "and", "at", "by", "can", "do", "does", "for", "from", "i",
    "in", "is", "it", "me", "my", "of", "or", "please", "s", "that", "the",
    "this", "to", "what", "with", "you",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

TOOL_SPEC = {
    "name": "resolve_tool",
    "description": "Resolve free-form user phrasing to the best-matching registered tool name.",
    "risk_level": "safe",
    "parameters": {
        "type": "object",
        "properties": {
            "phrase": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "aliases": {"type": "object"},
            "min_score": {"type": "number"},
        },
        "required": ["phrase", "tools"],
    },
}


def tokens(text):
    return [t for t in _TOKEN_RE.findall(str(text or "").lower()) if t not in STOPWORDS]


def _tool_words(tool_name):
    return tokens(str(tool_name).replace("_", " "))


def score(phrase_tokens, tool_tokens):
    """Overlap relative to the smaller set (0.0 - 1.0)."""
    left = set(phrase_tokens)
    right = set(tool_tokens)
    if not left or not right:
        return 0.0
    return round(len(left & right) / min(len(left), len(right)), 3)


def resolve_tool(phrase, catalog, aliases=None, min_score=0.5):
    """Best tool for `phrase` (or None) plus ranked candidates."""
    text = str(phrase or "").strip()
    if not text:
        return {"tool": None, "score": 0.0, "candidates": []}
    normalized = " ".join(tokens(text))
    for alias, tool in dict(aliases or {}).items():
        if " ".join(tokens(alias)) == normalized:
            return {"tool": str(tool), "score": 1.0, "candidates": []}
    phrase_tokens = tokens(text)
    scored = []
    for tool in dict.fromkeys(str(item) for item in catalog or []):
        value = score(phrase_tokens, _tool_words(tool))
        if value > 0:
            scored.append({"tool": tool, "score": value})
    scored.sort(key=lambda item: (-item["score"], item["tool"]))
    if not scored or scored[0]["score"] < float(min_score):
        best = scored[0]["score"] if scored else 0.0
        return {"tool": None, "score": best, "candidates": scored}
    return {"tool": scored[0]["tool"], "score": scored[0]["score"], "candidates": scored}


def handle(arguments):
    args = dict(arguments or {})
    phrase = args.get("phrase")
    tools = args.get("tools")
    if not str(phrase or "").strip():
        return {"success": False, "message": "Missing 'phrase'.", "data": {}}
    if not isinstance(tools, (list, tuple)) or not tools:
        return {"success": False,
                "message": "'tools' must be a non-empty list of tool names.",
                "data": {}}
    try:
        min_score = float(args.get("min_score") or 0.5)
    except (TypeError, ValueError):
        return {"success": False, "message": "'min_score' must be a number.", "data": {}}
    found = resolve_tool(phrase, list(tools), aliases=args.get("aliases"), min_score=min_score)
    if found["tool"]:
        return {"success": True,
                "message": f"Best tool: {found['tool']} (score {found['score']}).",
                "data": found}
    return {"success": False,
            "message": "No confident tool match; ask a clarifying question.",
            "data": found}
'''
    tests = '''
"""Tests for tool_naturalizer (generated by ProposalGenerator)."""
from __future__ import annotations

import unittest

import tool_naturalizer as tn

CATALOG = ["turn_on_device", "turn_off_device", "get_today_schedule", "play_media"]


class TokenTests(unittest.TestCase):
    def test_stopwords_removed(self):
        tokens = tn.tokens("what is on my schedule")
        self.assertIn("schedule", tokens)
        self.assertNotIn("what", tokens)
        self.assertNotIn("my", tokens)

    def test_tool_words_split_on_underscore(self):
        self.assertEqual(tn._tool_words("get_today_schedule"), ["get", "today", "schedule"])


class ResolveTests(unittest.TestCase):
    def test_schedule_phrase(self):
        found = tn.resolve_tool("what is on my schedule today", CATALOG)
        self.assertEqual(found["tool"], "get_today_schedule")
        self.assertGreaterEqual(found["score"], 0.5)

    def test_music_phrase(self):
        found = tn.resolve_tool("play some music", CATALOG)
        self.assertEqual(found["tool"], "play_media")

    def test_turn_off_beats_turn_on(self):
        found = tn.resolve_tool("turn off the kitchen light", CATALOG)
        self.assertEqual(found["tool"], "turn_off_device")

    def test_alias_exact_match_scores_one(self):
        found = tn.resolve_tool("lights", CATALOG, aliases={"lights": "turn_on_device"})
        self.assertEqual(found["tool"], "turn_on_device")
        self.assertEqual(found["score"], 1.0)

    def test_unknown_phrase_returns_none(self):
        found = tn.resolve_tool("book a flight to warsaw", CATALOG)
        self.assertIsNone(found["tool"])

    def test_min_score_gate(self):
        found = tn.resolve_tool("play some music", CATALOG, min_score=0.9)
        self.assertIsNone(found["tool"])


class HandleTests(unittest.TestCase):
    def test_success(self):
        out = tn.handle({"phrase": "what is on my schedule today", "tools": CATALOG})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["tool"], "get_today_schedule")

    def test_no_match_is_honest_failure(self):
        out = tn.handle({"phrase": "book a flight", "tools": CATALOG})
        self.assertFalse(out["success"])

    def test_missing_phrase(self):
        self.assertFalse(tn.handle({})["success"])

    def test_bad_tools_argument(self):
        out = tn.handle({"phrase": "music", "tools": "play_media"})
        self.assertFalse(out["success"])

    def test_tool_spec_shape(self):
        self.assertEqual(tn.TOOL_SPEC["name"], "resolve_tool")
        self.assertIn("phrase", tn.TOOL_SPEC["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
'''
    return GeneratedArtifact(
        module_name=module_name,
        tool_name="resolve_tool",
        code=code.strip() + "\n",
        tests=tests.strip() + "\n",
        test_module=f"test_{module_name}",
        sample_args={
            "phrase": "what is on my schedule today",
            "tools": ["get_today_schedule", "play_media"],
        },
    )


def _template_answer_quality(gap: CapabilityGap) -> GeneratedArtifact:
    module_name = "answer_confidence"
    code = '''
"""Confidence scoring + clarification trigger for answers (generated).

Answers below the confidence floor (no sources, missing required points,
too short) are reported as not confident so the assistant asks instead of
guessing.
"""
from __future__ import annotations

import re

MIN_CONFIDENT_SCORE = 0.7
_WORD_RE = re.compile(r"[a-z0-9]+")

TOOL_SPEC = {
    "name": "answer_confidence",
    "description": "Score an answer for specificity and sources; flag when to ask a clarifying question.",
    "risk_level": "safe",
    "parameters": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "question": {"type": "string"},
            "sources": {"type": "integer"},
            "required_terms": {"type": "array", "items": {"type": "string"}},
            "known_terms": {"type": "array", "items": {"type": "string"}},
        },
        "required": [],
    },
}


def words(text):
    return _WORD_RE.findall(str(text or "").lower())


def score_answer(answer, sources=0, required_terms=(), context_terms=()):
    """{score, confident, reasons} for a candidate answer."""
    text = str(answer or "").strip()
    if not text:
        return {"score": 0.0, "confident": False, "reasons": ["empty answer"]}
    reasons = []
    tokens = words(text)
    try:
        source_count = int(sources)
    except (TypeError, ValueError):
        source_count = 0
    if source_count < 0:
        source_count = 0
    required = [str(term).strip().lower() for term in required_terms or [] if str(term).strip()]
    score = 0.4 if len(tokens) >= 8 else 0.15
    if len(tokens) < 8:
        reasons.append("answer too short to be specific")
    score += min(source_count, 5) * 0.1
    if source_count < 1:
        reasons.append("no sources cited")
    covered = 1.0
    if required:
        lower = text.lower()
        missing = [term for term in required if term not in lower]
        covered = (len(required) - len(missing)) / len(required)
        score += 0.3 * covered
        if missing:
            reasons.append("missing required points: " + ", ".join(missing))
    score = round(min(score, 1.0), 2)
    confident = score >= MIN_CONFIDENT_SCORE and source_count >= 1 and covered >= 1.0
    if not confident and not reasons:
        reasons.append(f"confidence {score} below {MIN_CONFIDENT_SCORE}")
    return {"score": score, "confident": confident, "reasons": reasons}


def needs_clarification(user_text, known_terms=()):
    """True when the request is too thin or names none of the known entities."""
    text = str(user_text or "").strip()
    if not text:
        return True
    terms = [str(term).strip().lower() for term in known_terms or [] if str(term).strip()]
    if terms:
        lower = text.lower()
        if not any(term in lower for term in terms):
            return True
    return len(text.split()) < 4


def handle(arguments):
    args = dict(arguments or {})
    has_answer = bool(str(args.get("answer") or "").strip())
    has_question = bool(str(args.get("question") or "").strip())
    if not has_answer and not has_question:
        return {"success": False, "message": "Provide 'answer' and/or 'question'.", "data": {}}
    try:
        data = {}
        if has_answer:
            data.update(score_answer(
                args.get("answer"),
                sources=args.get("sources") or 0,
                required_terms=args.get("required_terms") or [],
            ))
        if has_question:
            data["needs_clarification"] = needs_clarification(
                args.get("question"),
                args.get("known_terms") or args.get("required_terms") or [],
            )
    except (TypeError, ValueError) as exc:
        return {"success": False, "message": "Invalid input: %s" % exc, "data": {}}
    confident = bool(data.get("confident", True))
    clarify = bool(data.get("needs_clarification", False))
    if not confident or clarify:
        return {"success": False,
                "message": "Answer is not confident enough - clarify or cite sources.",
                "data": data}
    return {"success": True,
            "message": f"Confident answer (score {data.get('score')}).",
            "data": data}
'''
    tests = '''
"""Tests for answer_confidence (generated by ProposalGenerator)."""
from __future__ import annotations

import unittest

import answer_confidence as ac

GOOD_ANSWER = (
    "The meeting moves to 16:00 on Tuesday according to the calendar entry "
    "and the team invite, so I updated the plan."
)


class ScoreTests(unittest.TestCase):
    def test_empty_answer(self):
        report = ac.score_answer("")
        self.assertEqual(report["score"], 0.0)
        self.assertFalse(report["confident"])

    def test_short_unsourced_answer_not_confident(self):
        report = ac.score_answer("It depends.")
        self.assertFalse(report["confident"])
        self.assertIn("no sources cited", report["reasons"])

    def test_specific_sourced_answer_confident(self):
        report = ac.score_answer(GOOD_ANSWER, sources=3, required_terms=["16:00", "tuesday"])
        self.assertTrue(report["confident"])
        self.assertGreaterEqual(report["score"], ac.MIN_CONFIDENT_SCORE)

    def test_missing_required_point_blocks_confidence(self):
        report = ac.score_answer(GOOD_ANSWER, sources=3, required_terms=["16:00", "bitcoin"])
        self.assertFalse(report["confident"])
        self.assertTrue(any("missing required points" in reason for reason in report["reasons"]))

    def test_bad_sources_value_is_treated_as_zero(self):
        report = ac.score_answer(GOOD_ANSWER, sources="many")
        self.assertFalse(report["confident"])


class ClarificationTests(unittest.TestCase):
    def test_short_request_needs_clarification(self):
        self.assertTrue(ac.needs_clarification("do the thing"))

    def test_request_without_known_entity_needs_clarification(self):
        self.assertTrue(ac.needs_clarification("move it to another day", ["meeting"]))

    def test_specific_request_is_clear(self):
        self.assertFalse(ac.needs_clarification("move the team meeting to tuesday", ["meeting"]))


class HandleTests(unittest.TestCase):
    def test_confident_answer_passes(self):
        out = ac.handle({"answer": GOOD_ANSWER, "sources": 3,
                         "required_terms": ["16:00", "tuesday"]})
        self.assertTrue(out["success"])
        self.assertIn("score", out["data"])

    def test_vague_answer_fails(self):
        out = ac.handle({"answer": "It depends.", "sources": 0})
        self.assertFalse(out["success"])

    def test_clarification_flag(self):
        out = ac.handle({"question": "reschedule it"})
        self.assertFalse(out["success"])
        self.assertTrue(out["data"]["needs_clarification"])

    def test_missing_arguments(self):
        self.assertFalse(ac.handle({})["success"])

    def test_tool_spec_shape(self):
        self.assertEqual(ac.TOOL_SPEC["name"], "answer_confidence")
        self.assertIn("answer", ac.TOOL_SPEC["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
'''
    return GeneratedArtifact(
        module_name=module_name,
        tool_name="answer_confidence",
        code=code.strip() + "\n",
        tests=tests.strip() + "\n",
        test_module=f"test_{module_name}",
        sample_args={
            "answer": (
                "The meeting moves to 16:00 on Tuesday according to the calendar "
                "entry and the team invite, so I updated the plan."
            ),
            "sources": 3,
            "required_terms": ["16:00", "tuesday"],
        },
    )


def _template_reliability(gap: CapabilityGap) -> GeneratedArtifact:
    module_name = "retry_policy"
    code = '''
"""Retry / backoff policy helpers (generated).

Permanent errors are never retried; transient ones back off exponentially
with an optional deterministic jitter.
"""
from __future__ import annotations

PERMANENT_ERRORS = frozenset({
    "invalid_argument", "permission_denied", "not_found", "unsupported",
    "validation_error", "unauthorized", "valueerror", "typeerror",
    "keyerror", "attributeerror",
})

TOOL_SPEC = {
    "name": "retry_policy",
    "description": "Compute retry backoff and decide whether a failed call should be retried.",
    "risk_level": "safe",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["backoff", "should_retry"]},
            "attempt": {"type": "integer"},
            "base": {"type": "number"},
            "cap": {"type": "number"},
            "jitter": {"type": "number"},
            "error_kind": {"type": "string"},
            "max_attempts": {"type": "integer"},
        },
        "required": ["action"],
    },
}


def compute_backoff(attempt, base=0.5, cap=8.0, jitter=0.1, rng=None):
    """Exponential backoff; jitter only applied when an rng is supplied."""
    if int(attempt) < 1:
        raise ValueError("attempt must be >= 1")
    if float(base) <= 0:
        raise ValueError("base must be > 0")
    ceiling = float(cap) if float(cap) > 0 else float(base)
    delay = min(ceiling, float(base) * (2 ** (int(attempt) - 1)))
    if rng is not None and jitter:
        factor = 1.0 + ((float(rng.random()) * 2.0) - 1.0) * float(jitter)
        delay = min(ceiling, max(0.0, delay * factor))
    return round(delay, 3)


def should_retry(error_kind, attempt, max_attempts=3):
    """False once attempts are exhausted or the error class is permanent."""
    if int(attempt) >= int(max_attempts):
        return False
    return str(error_kind or "").strip().lower() not in PERMANENT_ERRORS


def with_retries(operation, max_attempts=3, base=0.5, cap=8.0, sleep=None, rng=None):
    """Run `operation` until it succeeds or retries run out."""
    limit = int(max_attempts)
    if limit < 1:
        raise ValueError("max_attempts must be >= 1")
    attempt = 0
    last_error = ""
    while attempt < limit:
        attempt += 1
        try:
            return {"success": True, "attempts": attempt, "value": operation(), "error": ""}
        except Exception as exc:
            try:
                kind = str(exc.kind)
            except AttributeError:
                kind = type(exc).__name__
            last_error = str(exc)
            if not should_retry(kind, attempt, limit):
                return {"success": False, "attempts": attempt, "value": None,
                        "error": last_error}
            if attempt < limit and sleep is not None:
                sleep(compute_backoff(attempt, base=base, cap=cap, rng=rng))
    return {"success": False, "attempts": attempt, "value": None, "error": last_error}


def handle(arguments):
    args = dict(arguments or {})
    action = str(args.get("action") or "").strip().lower()
    try:
        if action == "backoff":
            delay = compute_backoff(
                int(args.get("attempt") or 1),
                base=float(args.get("base") or 0.5),
                cap=float(args.get("cap") or 8.0),
                jitter=float(args.get("jitter") or 0.0),
            )
            return {"success": True, "message": f"Backoff {delay}s.", "data": {"delay": delay}}
        if action == "should_retry":
            attempt = int(args.get("attempt") or 1)
            max_attempts = int(args.get("max_attempts") or 3)
            retry = should_retry(args.get("error_kind") or "", attempt, max_attempts)
            return {"success": True,
                    "message": "Retry." if retry else "Do not retry.",
                    "data": {"retry": retry, "attempt": attempt}}
        return {"success": False,
                "message": "Unknown action: %r" % (action or "<missing>"),
                "data": {}}
    except (TypeError, ValueError) as exc:
        return {"success": False, "message": "Invalid input: %s" % exc, "data": {}}
'''
    tests = '''
"""Tests for retry_policy (generated by ProposalGenerator)."""
from __future__ import annotations

import random
import unittest

import retry_policy as rp


class FlakyError(Exception):
    kind = "timeout"


class PermanentError(Exception):
    kind = "invalid_argument"


class BackoffTests(unittest.TestCase):
    def test_doubles_until_cap(self):
        self.assertEqual(rp.compute_backoff(1, base=0.5, cap=8.0, jitter=0.0), 0.5)
        self.assertEqual(rp.compute_backoff(2, base=0.5, cap=8.0, jitter=0.0), 1.0)
        self.assertEqual(rp.compute_backoff(4, base=0.5, cap=1.5, jitter=0.0), 1.5)

    def test_attempt_below_one_raises(self):
        with self.assertRaises(ValueError):
            rp.compute_backoff(0)

    def test_jitter_is_reproducible_with_seed(self):
        first = rp.compute_backoff(2, jitter=0.2, rng=random.Random(7))
        second = rp.compute_backoff(2, jitter=0.2, rng=random.Random(7))
        self.assertEqual(first, second)
        self.assertLessEqual(first, 1.2)
        self.assertGreaterEqual(first, 0.8)


class DecisionTests(unittest.TestCase):
    def test_timeout_is_retryable(self):
        self.assertTrue(rp.should_retry("timeout", 1, 3))

    def test_permanent_error_not_retried(self):
        self.assertFalse(rp.should_retry("invalid_argument", 1, 3))

    def test_exhausted_attempts_stop(self):
        self.assertFalse(rp.should_retry("timeout", 3, 3))


class WithRetriesTests(unittest.TestCase):
    def test_succeeds_after_transient_failures(self):
        calls = {"n": 0}
        sleeps = []

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise FlakyError("slow provider")
            return "done"

        out = rp.with_retries(flaky, max_attempts=3, sleep=sleeps.append)
        self.assertTrue(out["success"])
        self.assertEqual(out["value"], "done")
        self.assertEqual(out["attempts"], 3)
        self.assertEqual(len(sleeps), 2)

    def test_permanent_error_fails_fast(self):
        calls = {"n": 0}

        def broken():
            calls["n"] += 1
            raise PermanentError("bad argument")

        out = rp.with_retries(broken, max_attempts=3, sleep=lambda _delay: None)
        self.assertFalse(out["success"])
        self.assertEqual(out["attempts"], 1)
        self.assertEqual(calls["n"], 1)


class HandleTests(unittest.TestCase):
    def test_backoff_action(self):
        out = rp.handle({"action": "backoff", "attempt": 3, "base": 0.5, "jitter": 0.0})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["delay"], 2.0)

    def test_should_retry_action(self):
        out = rp.handle({"action": "should_retry", "error_kind": "timeout", "attempt": 1})
        self.assertTrue(out["success"])
        self.assertTrue(out["data"]["retry"])

    def test_unknown_action(self):
        self.assertFalse(rp.handle({})["success"])

    def test_bad_attempt_value(self):
        self.assertFalse(rp.handle({"action": "backoff", "attempt": "soon"})["success"])

    def test_tool_spec_shape(self):
        self.assertEqual(rp.TOOL_SPEC["name"], "retry_policy")
        self.assertIn("action", rp.TOOL_SPEC["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
'''
    return GeneratedArtifact(
        module_name=module_name,
        tool_name="retry_policy",
        code=code.strip() + "\n",
        tests=tests.strip() + "\n",
        test_module=f"test_{module_name}",
        sample_args={"action": "backoff", "attempt": 2, "jitter": 0.0},
    )


def _template_general(gap: CapabilityGap) -> GeneratedArtifact:
    module_name = "structured_result"
    code = '''
"""Normalise tool output into the {success, message, data} contract (generated)."""
from __future__ import annotations

CONTRACT_KEYS = ("success", "message", "data", "error")

TOOL_SPEC = {
    "name": "structured_result",
    "description": "Normalise any tool return value into {success, message, data, error}.",
    "risk_level": "safe",
    "parameters": {
        "type": "object",
        "properties": {
            "value": {"description": "Raw tool return value"},
            "tool_name": {"type": "string"},
        },
        "required": ["value"],
    },
}


def normalize_result(value, tool_name=""):
    """Anything -> {success, message, data, error}."""
    if isinstance(value, dict):
        success = bool(value.get("success", True))
        message = str(value.get("message") or ("ok" if success else "failed"))
        data = dict(value.get("data") or {})
        for key, item in value.items():
            if key not in CONTRACT_KEYS:
                data.setdefault(key, item)
        return {"success": success, "message": message, "data": data,
                "error": str(value.get("error") or "")}
    if isinstance(value, bool):
        return {"success": value, "message": "ok" if value else "failed", "data": {},
                "error": "" if value else "failure"}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {"success": False, "message": "empty result", "data": {},
                    "error": "empty_result"}
        return {"success": True, "message": text, "data": {}, "error": ""}
    if value is None:
        return {"success": False, "message": "no result", "data": {}, "error": "no_result"}
    return {"success": True, "message": str(value), "data": {"value": value}, "error": ""}


def merge_data(*parts):
    out = {}
    for part in parts:
        if isinstance(part, dict):
            out.update(part)
    return out


def handle(arguments):
    args = dict(arguments or {})
    if "value" not in args:
        return {"success": False, "message": "Missing 'value'.", "data": {}}
    tool_name = str(args.get("tool_name") or "")
    result = normalize_result(args.get("value"), tool_name=tool_name)
    if tool_name:
        result["data"] = merge_data(result.get("data"), {"tool_name": tool_name})
    return result
'''
    tests = '''
"""Tests for structured_result (generated by ProposalGenerator)."""
from __future__ import annotations

import unittest

import structured_result as sr


class NormalizeTests(unittest.TestCase):
    def test_string_becomes_success(self):
        out = sr.normalize_result("all good")
        self.assertTrue(out["success"])
        self.assertEqual(out["message"], "all good")

    def test_failed_dict_keeps_error_and_extra_keys(self):
        out = sr.normalize_result({"success": False, "message": "boom", "error": "timeout",
                                   "code": 42})
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "timeout")
        self.assertEqual(out["data"]["code"], 42)

    def test_none_is_failure(self):
        out = sr.normalize_result(None)
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "no_result")

    def test_boolean(self):
        self.assertTrue(sr.normalize_result(True)["success"])
        self.assertFalse(sr.normalize_result(False)["success"])

    def test_merge_data(self):
        self.assertEqual(sr.merge_data({"a": 1}, {"b": 2}, None, {"a": 3}),
                         {"a": 3, "b": 2})


class HandleTests(unittest.TestCase):
    def test_missing_value(self):
        self.assertFalse(sr.handle({})["success"])

    def test_success_with_tool_name(self):
        out = sr.handle({"value": "done", "tool_name": "demo"})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["tool_name"], "demo")

    def test_dict_value(self):
        out = sr.handle({"value": {"success": True, "message": "ok", "data": {"n": 1}}})
        self.assertTrue(out["success"])
        self.assertEqual(out["data"]["n"], 1)

    def test_tool_spec_shape(self):
        self.assertEqual(sr.TOOL_SPEC["name"], "structured_result")
        self.assertEqual(sr.TOOL_SPEC["parameters"]["required"], ["value"])


if __name__ == "__main__":
    unittest.main()
'''
    return GeneratedArtifact(
        module_name=module_name,
        tool_name="structured_result",
        code=code.strip() + "\n",
        tests=tests.strip() + "\n",
        test_module=f"test_{module_name}",
        sample_args={"value": "done", "tool_name": "demo"},
    )


TEMPLATES: Dict[str, Callable[[CapabilityGap], GeneratedArtifact]] = {
    "scheduling": _template_scheduling,
    "tool_lookup": _template_tool_lookup,
    "answer_quality": _template_answer_quality,
    "reliability": _template_reliability,
    "general": _template_general,
}

#: Requirement text per gap kind — what the assistant says it needs.
REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "scheduling": {
        "title": "Robust recurring event handling",
        "statement": (
            "I need a more robust way to handle recurring calendar events: expand "
            "daily/weekly/monthly rules (including intervals like 'every other "
            "Friday'), compute the next occurrence, and describe a rule in plain "
            "language without depending on a calendar backend."
        ),
        "acceptance": [
            "Expands daily/weekly/monthly rules to N occurrences (interval + byweekday)",
            "Computes the next occurrence on/after a given date",
            "Rejects malformed rules with a clear message instead of raising",
            "Exposes a recurring_schedule tool through the ToolRegistry hot-swap contract",
            "Generated unit tests pass in the Execution Sandbox",
        ],
    },
    "tool_lookup": {
        "title": "Natural-language tool resolution",
        "statement": (
            "I keep logging NO_TOOL_MATCH before I even try the obvious tool. I need "
            "a deterministic way to map what the user says onto an existing tool name "
            "and to admit when no confident match exists."
        ),
        "acceptance": [
            "Resolves common phrasings to the correct registered tool name",
            "Supports explicit alias phrases with exact-match priority",
            "Returns no match (instead of guessing) below the confidence threshold",
            "Generated unit tests pass in the Execution Sandbox",
        ],
    },
    "answer_quality": {
        "title": "Answer confidence and clarification",
        "statement": (
            "I gave vague answers because nothing forced me to check them. I need "
            "confidence scoring over sources + required points, and a clarification "
            "trigger when the request is too thin to answer."
        ),
        "acceptance": [
            "Scores an answer on length, sources and required points",
            "Blocks answers below the confidence floor",
            "Flags requests that need a clarifying question first",
            "Generated unit tests pass in the Execution Sandbox",
        ],
    },
    "reliability": {
        "title": "Retry and timeout policy",
        "statement": (
            "My tools fail silently or time out. I need a shared retry policy: "
            "exponential backoff for transient errors, fail-fast for permanent ones, "
            "and a single place that decides whether to retry."
        ),
        "acceptance": [
            "Computes capped exponential backoff with optional deterministic jitter",
            "Never retries permanent error classes",
            "Stops after the configured attempt budget",
            "Generated unit tests pass in the Execution Sandbox",
        ],
    },
    "general": {
        "title": "Structured tool results",
        "statement": (
            "Tool outputs arrive as bare strings and half-dicts, which makes every "
            "downstream check guess. I need one normaliser to the "
            "{success, message, data, error} contract."
        ),
        "acceptance": [
            "Normalises strings, dicts, booleans and None to the contract",
            "Preserves error codes and extra keys in data",
            "Generated unit tests pass in the Execution Sandbox",
        ],
    },
}


class ProposalGenerator:
    """Turns a CapabilityGap into a Proposal (requirement + code + tests)."""

    def __init__(
        self,
        analyzer: Optional[GapAnalyzer] = None,
        store: Optional[ProposalStore] = None,
    ):
        self.analyzer = analyzer or GapAnalyzer()
        self.store = store or ProposalStore()

    def get_gap(self, gap_id: str) -> CapabilityGap:
        for gap in self.analyzer.load():
            if gap.id == str(gap_id or ""):
                return gap
        raise KeyError(f"Unknown gap: {gap_id} (run `gaps` first)")

    def for_gap(self, gap: CapabilityGap) -> Proposal:
        template = TEMPLATES.get(gap.kind, TEMPLATES["general"])
        artifact = template(gap)
        spec = REQUIREMENTS.get(gap.kind, REQUIREMENTS["general"])
        statement = str(spec["statement"])
        acceptance = list(spec["acceptance"])
        observed = str(getattr(gap, "observed_tool", "") or "").strip()
        error_code = str(getattr(gap, "error_code", "") or "").strip()
        if observed:
            # Keep template codegen/tool_name; enrich the human-readable requirement
            # so proposals name the concrete runtime failure, not only the domain.
            detail = (
                f"Runtime evidence points at tool `{observed}` in the "
                f"{gap.domain} domain"
            )
            if error_code:
                detail += f" (error_code={error_code})"
            statement = f"{detail}. {statement}"
            acceptance = [
                f"Addresses observed runtime tool: {observed}"
                + (f" (error_code={error_code})" if error_code else ""),
                *acceptance,
            ]
        elif error_code:
            statement = (
                f"Runtime evidence reports error_code={error_code} in the "
                f"{gap.domain} domain. {statement}"
            )
        requirement = Requirement(
            id=f"req_{gap.id}",
            title=spec["title"],
            statement=statement,
            acceptance=acceptance,
        )
        proposal_id = f"prop_{_slug(gap.kind)}_{_slug(gap.domain)}_{uuid.uuid4().hex[:6]}"
        return Proposal(
            id=proposal_id,
            gap_id=gap.id,
            gap_name=gap.name,
            domain=gap.domain,
            kind=gap.kind,
            requirement=requirement,
            module_name=artifact.module_name,
            tool_name=artifact.tool_name,
            code=artifact.code,
            tests=artifact.tests,
            test_module=artifact.test_module,
            sample_args=dict(artifact.sample_args),
            status=ProposalStatus.DRAFT.value,
            created_at=utc_now_iso(),
            evidence_ids=list(gap.sample_evidence),
        )

    def propose(self, gap_id: str) -> Proposal:
        """Generate + persist a proposal for a stored gap."""
        proposal = self.for_gap(self.get_gap(gap_id))
        return self.store.put(proposal)

    def update(self, proposal: Proposal) -> Proposal:
        return self.store.put(proposal)
