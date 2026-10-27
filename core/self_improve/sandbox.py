# ==========================
# ELIAN — Self-Improvement / Execution Sandbox
# ==========================
"""Runs freshly generated code before it is allowed anywhere near the core.

Isolation properties (defence in depth, not a security boundary):
- generated files live only under ``Data/self_improve/sandbox/<proposal_id>/``
- static AST safety scan first (import allowlist, no eval/open/dunder access)
- child processes get a scrubbed environment, no project PYTHONPATH
- hard timeout on every subprocess run
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.self_improve.experience_log import utc_now_iso
from core.self_improve.proposal import Proposal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "Data" / "self_improve"
SANDBOX_ROOT = DATA_DIR / "sandbox"
DEFAULT_TIMEOUT_S = 30.0

#: Imports generated code may use (plus nothing else).
ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__",
        "bisect",
        "calendar",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "heapq",
        "itertools",
        "json",
        "math",
        "operator",
        "random",
        "re",
        "statistics",
        "string",
        "textwrap",
        "typing",
        "unittest",
    }
)

#: Dunder attributes that are harmless (everything else is rejected).
ALLOWED_DUNDERS = frozenset({"__name__"})

#: Dunder-looking string literals that are harmless (``__main__`` guard).
ALLOWED_STRING_DUNDERS = frozenset({"__main__"})

_DUNDER_RE = re.compile(r"__[A-Za-z0-9_]+__")

#: Calls that can escape the sandbox or touch the filesystem/network.
BANNED_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "quit",
        "setattr",
        "vars",
    }
)

_INTERFACE_SCRIPT = '''"""Interface probe for generated tool modules (run inside the sandbox).

Catches the defect class that per-action unit tests cannot see:

1. ``alias_params``     - two schema names for one concept (weekday/byweekday).
   Actions then drift: one honours ``byweekday``, another ``weekday``, and the
   second fails with a confusing "missing" error when called the natural way.
2. ``action_failures``  - a declared action that cannot be invoked using only
   the parameters its own schema advertises.
3. ``weekday_mismatch`` - returned dates that do not fall on the weekday the
   caller actually asked for. Silent wrong data is worse than a crash.
4. ``message_polish``   - raw enum/abbreviation leakage in user-facing text.
"""

import datetime as _dt
import importlib
import json
import re
import sys

report = {"alias_params": [], "action_failures": [], "weekday_mismatch": [],
          "message_polish": [], "actions_tested": [], "errors": [],
          "skipped": ""}

DAY_NAMES = {"mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1,
             "wed": 2, "weds": 2, "wednesday": 2, "thu": 3, "thur": 3,
             "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
             "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
             "pn": 0, "wt": 1, "sr": 2, "cz": 3, "pt": 4, "so": 5, "nd": 6}
ISO_RE = re.compile(r"\\b(\\d{4}-\\d{2}-\\d{2})\\b")
SNAKE_RE = re.compile(r"\\b[a-z]+_[a-z_]+\\b")
WEEKDAY_WORD_RE = re.compile(
    r"\\b(mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)\\b", re.I)


def _concept(name, all_names):
    """Normalise a parameter name to the concept it names.

    A prefix is only stripped when the bare form is *also* declared, so
    ``byweekday``/``weekday`` collapse but ``byte``/``count`` do not.
    """
    key = str(name or "").strip().lower().replace("-", "_")
    for prefix in ("by_", "my_", "the_"):
        if key.startswith(prefix) and key[len(prefix):] in all_names:
            key = key[len(prefix):]
    if key.startswith("by") and len(key) > 2 and key[2:] in all_names:
        key = key[2:]
    if key.endswith("ies"):
        key = key[:-3] + "y"
    elif key.endswith("ses"):
        key = key[:-2]
    elif key.endswith("s") and key[:-1] in all_names:
        key = key[:-1]
    return key


_SKIP = object()

#: A Wednesday on purpose: a Monday anchor hides weekday-alignment bugs.
_DATE_SAMPLE = "2026-01-07"


def _sample_for(name, schema):
    """A plausible value for a declared parameter, or ``_SKIP`` when the
    schema does not say enough to guess one (never invent a value)."""
    key = str(name or "").lower()
    description = ""
    kind = ""
    enum = None
    if isinstance(schema, dict):
        description = str(schema.get("description") or "").lower()
        kind = str(schema.get("type") or "").lower()
        if isinstance(schema.get("enum"), list) and schema.get("enum"):
            enum = schema["enum"]
    # Structured kinds first: a numeric parameter must never receive a date,
    # however often the word "date" appears in its description.
    if kind in ("integer", "number"):
        return 1
    if kind == "boolean":
        return True
    if kind == "array":
        item = _sample_for(key, (schema or {}).get("items") or {})
        return _SKIP if item is _SKIP else [item]
    if enum:
        return enum[0]
    if kind and kind != "string":
        return _SKIP
    hint = key + " " + description
    if key == "until":
        return "2027-12-31"
    if "date" in hint or key.endswith("_at") or key.endswith("_on"):
        return "2026-01-01" if key == "from_date" else _DATE_SAMPLE
    if "weekday" in hint or key.endswith("_day") or "day" in key.split("_"):
        return "mon"
    return _SKIP


def _requested_weekdays(arguments):
    """Weekdays the caller asked for, from any day-named parameter."""
    wanted = set()
    for name, value in (arguments or {}).items():
        if "day" not in str(name).lower():
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item.strip().lower() in DAY_NAMES:
                wanted.add(DAY_NAMES[item.strip().lower()])
    return wanted


def _dates_in(value, found):
    if isinstance(value, str):
        found.extend(ISO_RE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            _dates_in(item, found)
    elif isinstance(value, list):
        for item in value:
            _dates_in(item, found)
    return found


try:
    module = importlib.import_module(sys.argv[1])
except Exception as exc:  # noqa: BLE001 - report, don't raise
    report["errors"].append("import failed: %s: %s" % (type(exc).__name__, exc))
    print(json.dumps(report))
    sys.exit(1)

handle = getattr(module, "handle", None)
spec = getattr(module, "TOOL_SPEC", None) or {}
properties = ((spec.get("parameters") or {}).get("properties") or {})

if not callable(handle):
    report["errors"].append("handle() missing - interface probe cannot run")
    print(json.dumps(report))
    sys.exit(1)

# 1. one name per concept
groups = {}
for name in properties:
    groups.setdefault(_concept(name, set(properties)), []).append(name)
for concept, names in sorted(groups.items()):
    if len(names) > 1:
        report["alias_params"].append(
            "%s: %s (one name per concept)" % (concept, ", ".join(sorted(names))))

# 2./3. every declared action, called with only what its schema advertises
action_schema = properties.get("action") or {}
actions = action_schema.get("enum") if isinstance(action_schema, dict) else None
base = {}
for name, schema in properties.items():
    if name == "action":
        continue
    value = _sample_for(name, schema)
    if value is not _SKIP:
        base[name] = value

if not actions:
    # No action dispatch in this tool, so the cross-action probe does not apply.
    # That is a skipped check, never a failure.
    report["skipped"] = "no 'action' enum in TOOL_SPEC"
    print(json.dumps(report))
    sys.exit(0)

# Asking for weekday names implies a weekly rule; keep the caller coherent so
# the probe never penalises a module for a nonsensical argument mix.
if _requested_weekdays(base) and isinstance(base.get("freq"), str):
    weekly = [v for v in (action_schema and properties.get("freq", {}).get("enum") or [])
              if isinstance(v, str) and "week" in v.lower()]
    if weekly:
        base["freq"] = weekly[0]

for action in actions:
    arguments = dict(base)
    arguments["action"] = action
    report["actions_tested"].append(action)
    try:
        out = handle(arguments)
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        report["action_failures"].append("%s raised %s: %s"
                                         % (action, type(exc).__name__, exc))
        continue
    if not isinstance(out, dict):
        report["action_failures"].append("%s returned %r" % (action, out))
        continue
    if out.get("success") is not True:
        report["action_failures"].append(
            "%s failed with its own schema's arguments: %s"
            % (action, str(out.get("message"))[:120]))
        continue

    message = str(out.get("message") or "")
    if SNAKE_RE.search(message):
        report["message_polish"].append("%s: internal id in message %r" % (action, message))
    if WEEKDAY_WORD_RE.search(message):
        report["message_polish"].append(
            "%s: raw weekday abbreviation in message %r" % (action, message))

    wanted = _requested_weekdays(arguments)
    if wanted:
        for iso in sorted(set(_dates_in(out.get("data"), []))):
            try:
                actual = _dt.date.fromisoformat(iso).weekday()
            except ValueError:
                continue
            if actual not in wanted:
                asked = ", ".join(
                    _dt.date(2026, 1, 5 + index).strftime("%A")
                    for index in sorted(wanted))
                report["weekday_mismatch"].append(
                    "%s: asked for %s, got %s (%s)"
                    % (action, asked, iso,
                       _dt.date.fromisoformat(iso).strftime("%A")))

print(json.dumps(report))
sys.exit(0)
'''

_CONTRACT_SCRIPT = '''"""Contract probe for generated tool modules (run inside the sandbox)."""
import importlib
import json
import sys

report = {"imported": False, "spec_ok": False, "empty_ok": False,
          "sample_ok": False, "errors": []}

try:
    module = importlib.import_module(sys.argv[1])
    report["imported"] = True
except Exception as exc:  # noqa: BLE001 - report, don't raise
    report["errors"].append("import failed: %s: %s" % (type(exc).__name__, exc))
    print(json.dumps(report))
    sys.exit(1)

sample = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

try:
    spec = getattr(module, "TOOL_SPEC", None)
    handle = getattr(module, "handle", None)
    if not isinstance(spec, dict):
        report["errors"].append("TOOL_SPEC missing or not a dict")
    else:
        name = spec.get("name")
        params = spec.get("parameters")
        description = spec.get("description")
        if not isinstance(name, str) or not name.strip():
            report["errors"].append("TOOL_SPEC.name must be a non-empty string")
        elif not isinstance(description, str) or not description.strip():
            report["errors"].append("TOOL_SPEC.description must be a non-empty string")
        elif not isinstance(params, dict) or not isinstance(params.get("properties"), dict):
            report["errors"].append("TOOL_SPEC.parameters.properties must be a dict")
        else:
            report["spec_ok"] = True
    if not callable(handle):
        report["errors"].append("handle() is missing or not callable")
    else:
        empty = handle({})
        ok = (isinstance(empty, dict)
              and isinstance(empty.get("success"), bool)
              and isinstance(empty.get("message"), str)
              and isinstance(empty.get("data"), dict))
        report["empty_ok"] = bool(ok)
        if not ok:
            report["errors"].append("handle({}) violates the contract: %r" % (empty,))
        happy = handle(sample)
        happy_ok = (isinstance(happy, dict) and happy.get("success") is True
                    and isinstance(happy.get("message"), str)
                    and isinstance(happy.get("data"), dict))
        report["sample_ok"] = bool(happy_ok)
        if not happy_ok:
            report["errors"].append("handle(sample_args) did not succeed: %r" % (happy,))
except Exception as exc:  # noqa: BLE001 - report, don't raise
    report["errors"].append("probe failed: %s: %s" % (type(exc).__name__, exc))

print(json.dumps(report))
sys.exit(0 if (report["imported"] and report["spec_ok"] and report["empty_ok"]
               and report["sample_ok"]) else 1)
'''


@dataclass
class SafetyReport:
    passed: bool
    findings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "findings": list(self.findings)}


def scan_source(
    source: str,
    filename: str = "module.py",
    *,
    allowed_extra: Iterable[str] = (),
) -> SafetyReport:
    """Static AST scan of generated code. Fails closed on anything suspicious."""
    findings: List[str] = []
    allowed = ALLOWED_IMPORT_ROOTS | {str(item) for item in allowed_extra}
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return SafetyReport(False, [f"{filename}: syntax error: {exc}"])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed:
                    findings.append(f"{filename}:{node.lineno}: import of '{root}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            root = str(node.module or "").split(".")[0]
            if root and root not in allowed:
                findings.append(
                    f"{filename}:{node.lineno}: from-import of '{root}' is not allowed"
                )
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else ""
            if name in BANNED_CALLS:
                findings.append(f"{filename}:{node.lineno}: call to '{name}()' is not allowed")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if (
                attr.startswith("__")
                and attr.endswith("__")
                and len(attr) > 2
                and attr not in ALLOWED_DUNDERS
            ):
                findings.append(
                    f"{filename}:{node.lineno}: dunder attribute '{attr}' is not allowed"
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for hit in sorted(set(_DUNDER_RE.findall(node.value))):
                if hit not in ALLOWED_STRING_DUNDERS:
                    findings.append(
                        f"{filename}:{node.lineno}: string literal '{hit}' is not allowed"
                    )
    return SafetyReport(passed=not findings, findings=findings)


@dataclass
class CommandResult:
    returncode: int
    output: str
    duration_s: float
    timed_out: bool

    @property
    def passed(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class ExecutionSandbox:
    """Materialises a proposal and runs it in scrubbed subprocesses."""

    def __init__(
        self,
        root: Path | str = SANDBOX_ROOT,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        self.root = Path(root)
        self.timeout_s = float(timeout_s)

    # ---- files -------------------------------------------------------
    def directory_for(self, proposal: Proposal) -> Path:
        return self.root / proposal.id

    def materialize(self, proposal: Proposal, *, clean: bool = True) -> Path:
        directory = self.directory_for(proposal)
        if clean and directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{proposal.module_name}.py").write_text(proposal.code, encoding="utf-8")
        (directory / f"{proposal.test_module}.py").write_text(proposal.tests, encoding="utf-8")
        (directory / "_contract_probe.py").write_text(_CONTRACT_SCRIPT, encoding="utf-8")
        (directory / "_interface_probe.py").write_text(_INTERFACE_SCRIPT, encoding="utf-8")
        (directory / "proposal.json").write_text(
            json.dumps(
                {
                    "proposal_id": proposal.id,
                    "gap": proposal.gap_name,
                    "tool": proposal.tool_name,
                    "created_at": utc_now_iso(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return directory

    # ---- checks ------------------------------------------------------
    def safety_scan(self, proposal: Proposal) -> SafetyReport:
        # The test file is allowed to import the module under test.
        module_report = scan_source(proposal.code, f"{proposal.module_name}.py")
        test_report = scan_source(
            proposal.tests,
            f"{proposal.test_module}.py",
            allowed_extra=(proposal.module_name,),
        )
        findings = list(module_report.findings) + list(test_report.findings)
        return SafetyReport(passed=not findings, findings=findings)

    def import_check(self, proposal: Proposal) -> CommandResult:
        directory = self.materialize(proposal)
        return self.run_python(
            ["-c", f"import {proposal.module_name}"], cwd=directory
        )

    def run_tests(self, proposal: Proposal) -> CommandResult:
        directory = self.materialize(proposal)
        return self.run_python(
            ["-m", "unittest", "-v", proposal.test_module], cwd=directory
        )

    def run_contract(self, proposal: Proposal) -> Dict[str, Any]:
        directory = self.materialize(proposal)
        result = self.run_python(
            ["_contract_probe.py", proposal.module_name, json.dumps(proposal.sample_args)],
            cwd=directory,
        )
        report: Dict[str, Any] = {
            "passed": result.passed,
            "output": result.output.strip(),
            "duration_s": result.duration_s,
        }
        for line in reversed(result.output.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    report.update(json.loads(line))
                except json.JSONDecodeError:
                    pass
                break
        if result.timed_out:
            report["passed"] = False
            report.setdefault("errors", []).append("contract probe timed out")
        return report

    def run_interface(self, proposal: Proposal) -> Dict[str, Any]:
        """Cross-action interface probe: aliases, unreachable actions,
        weekday mismatches and unpolished user-facing messages."""
        directory = self.materialize(proposal)
        result = self.run_python(
            ["_interface_probe.py", proposal.module_name], cwd=directory
        )
        report: Dict[str, Any] = {
            "alias_params": [],
            "action_failures": [],
            "weekday_mismatch": [],
            "message_polish": [],
            "actions_tested": [],
            "errors": [],
            "skipped": "",
            "output": result.output.strip(),
            "duration_s": result.duration_s,
        }
        for line in reversed(result.output.strip().splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    report.update(json.loads(line))
                except json.JSONDecodeError:
                    pass
                break
        if result.timed_out:
            report.setdefault("errors", []).append("interface probe timed out")
        return report

    # ---- primitives --------------------------------------------------
    def run_python(self, args: List[str], cwd: Path) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, *args],
                cwd=str(cwd),
                env=self._scrubbed_env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            return CommandResult(
                returncode=completed.returncode,
                output=output,
                duration_s=round(time.monotonic() - started, 3),
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "")
            if isinstance(output, bytes):
                output = output.decode("utf-8", "replace")
            return CommandResult(
                returncode=-1,
                output=f"timed out after {self.timeout_s}s\n{output}",
                duration_s=round(time.monotonic() - started, 3),
                timed_out=True,
            )
        except OSError as exc:
            return CommandResult(
                returncode=-1,
                output=f"sandbox launch failed: {exc}",
                duration_s=round(time.monotonic() - started, 3),
                timed_out=False,
            )

    @staticmethod
    def _scrubbed_env() -> Dict[str, str]:
        env: Dict[str, str] = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        for key in ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR", "TMPDIR", "TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        # Interpreter location only — never the project PYTHONPATH.
        for key in ("PYTHONHOME", "PYTHONEXECUTABLE"):
            value = os.environ.get(key)
            if value:
                env[key] = value
        return env


def get_sandbox() -> ExecutionSandbox:
    return ExecutionSandbox()
