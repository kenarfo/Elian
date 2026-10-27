# ==========================
# ELIAN — Self-Improvement / Tool hot-swap
# ==========================
"""Adoption: verified + human-approved code becomes a live ToolRegistry tool.

Learned modules live in ``core/self_improve/learned/`` and are imported at
runtime, so new capabilities can be added or swapped without restarting the
assistant. Safety rules baked in here:

- only ``APPROVED`` proposals may be adopted (human-in-the-loop gate)
- learned tools can never shadow a builtin/registered tool — the load skips
  any name that is already taken by a non-learned tool
- unlearning deletes the file and unregisters the tool
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.persistence import safe_read_json
from core.risk import RiskLevel
from core.self_improve.experience_log import utc_now_iso
from core.self_improve.proposal import Proposal, ProposalStatus, ProposalStore
from core.tool_result import ToolResult
from core.tools import Tool, ToolRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "Data" / "self_improve"
LEARNED_DIR = Path(__file__).resolve().parent / "learned"
MANIFEST_PATH = DATA_DIR / "adopted.json"
MODULE_PACKAGE = "core.self_improve.learned"

_RISK = {
    "safe": RiskLevel.SAFE,
    "medium": RiskLevel.MEDIUM,
    "dangerous": RiskLevel.DANGEROUS,
}


class AdoptionError(Exception):
    """Raised when adoption is refused (gate closed / bad state)."""


# =========================================================================
# Tool construction
# =========================================================================
def _wrap_handler(tool_name: str, fn):
    def handler(arguments) -> ToolResult:
        try:
            output = fn(dict(arguments or {})) or {}
        except Exception as exc:  # noqa: BLE001 - learned code is untrusted
            return ToolResult.fail(
                f"{tool_name} nie powiodło się: {exc}",
                tool_name=tool_name,
                error="learned_tool_error",
            )
        if not isinstance(output, dict):
            return ToolResult.fail(
                f"{tool_name} zwróciło wynik w złym formacie.",
                tool_name=tool_name,
                error="learned_tool_contract",
            )
        message = str(output.get("message") or "")
        raw_data = output.get("data")
        data = dict(raw_data) if isinstance(raw_data, dict) else {}
        if output.get("success"):
            return ToolResult.ok(message or "ok", tool_name=tool_name, data=data)
        return ToolResult.fail(
            message or "learned tool failed.",
            tool_name=tool_name,
            error=str(output.get("error") or "learned_tool_failed"),
            data=data,
        )

    return handler


def build_tool(module, module_name: str) -> Tool:
    """Turn a learned module (TOOL_SPEC + handle) into a registry Tool."""
    spec = getattr(module, "TOOL_SPEC", None)
    fn = getattr(module, "handle", None)
    if not isinstance(spec, dict):
        raise ValueError(f"{module_name}: TOOL_SPEC missing")
    if not callable(fn):
        raise ValueError(f"{module_name}: handle() missing")
    name = str(spec.get("name") or "").strip()
    if not name:
        raise ValueError(f"{module_name}: TOOL_SPEC.name empty")
    params = spec.get("parameters")
    params = dict(params) if isinstance(params, dict) else {}
    risk = _RISK.get(str(spec.get("risk_level") or "safe").lower(), RiskLevel.SAFE)
    try:
        timeout = float(spec.get("timeout") or 15.0)
    except (TypeError, ValueError):
        timeout = 15.0
    tool = Tool(
        name=name,
        description=str(spec.get("description") or f"Learned tool {name}."),
        handler=_wrap_handler(name, fn),
        parameters=params,
        risk_level=risk,
        available=True,
        timeout=timeout,
    )
    setattr(tool, "learned_module", str(module_name))
    return tool


# =========================================================================
# Learned module loading (hot-swap)
# =========================================================================
def learned_sources() -> Dict[str, Path]:
    """module stem → file path for everything in learned/."""
    if not LEARNED_DIR.exists():
        return {}
    return {
        path.stem: path
        for path in sorted(LEARNED_DIR.glob("*.py"))
        if path.name != "__init__.py" and not path.name.startswith("_")
    }


def _import_learned(module_name: str, path: Path):
    full_name = f"{MODULE_PACKAGE}.{module_name}"
    if full_name in sys.modules:
        return importlib.reload(sys.modules[full_name])
    return importlib.import_module(full_name)


# module stem -> (path, mtime_ns, tool name) of what we already loaded
_LOADED: Dict[str, Dict[str, Any]] = {}


def load_learned_tools(registry: ToolRegistry, *, replace: bool = True) -> List[str]:
    """Register (or hot-swap) every learned tool. Never shadows other tools.

    Cheap enough to call on every ``get_registry()``: unchanged files are
    skipped via the mtime cache, and tools whose source file disappeared are
    pruned from the registry.
    """
    sources = learned_sources()
    if not all(
        callable(getattr(registry, attr, None))
        for attr in ("get", "register", "unregister")
    ):
        # Test doubles without the registry API: nothing to hot-swap into.
        return []
    loaded: List[str] = []
    for module_name, path in sources.items():
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        cached = _LOADED.get(module_name) or {}
        existing = registry.get(str(cached.get("tool_name") or ""))
        if (
            cached.get("mtime_ns") == mtime
            and existing is not None
            and getattr(existing, "learned_module", "") == module_name
        ):
            continue
        try:
            module = _import_learned(module_name, path)
            tool = build_tool(module, module_name)
        except Exception as exc:  # noqa: BLE001 - one bad file must not kill startup
            print(f"[SELF_IMPROVE] learned module '{module_name}' skipped: {exc}")
            continue
        current = registry.get(tool.name)
        if current is not None:
            if getattr(current, "learned_module", "") != module_name:
                print(
                    f"[SELF_IMPROVE] learned tool '{tool.name}' skipped: name already "
                    "owned by a builtin tool"
                )
                continue
            if not replace:
                continue
            registry.unregister(tool.name)
        registry.register(tool)
        _LOADED[module_name] = {
            "path": str(path),
            "mtime_ns": mtime,
            "tool_name": tool.name,
        }
        loaded.append(tool.name)

    # Prune learned tools whose source file was deleted (unlearn without restart).
    for module_name in list(_LOADED):
        if module_name in sources:
            continue
        record = _LOADED.pop(module_name)
        tool = registry.get(str(record.get("tool_name") or ""))
        if tool is not None and getattr(tool, "learned_module", "") == module_name:
            registry.unregister(tool.name)
    return loaded


def unregister_learned_tool(registry: ToolRegistry, tool_name: str) -> bool:
    tool = registry.get(tool_name)
    if tool is None or not getattr(tool, "learned_module", ""):
        return False
    registry.unregister(tool_name)
    return True


# =========================================================================
# Manifest
# =========================================================================
def load_manifest() -> Dict[str, Any]:
    raw, _ = safe_read_json(MANIFEST_PATH, default={"modules": {}})
    if isinstance(raw, dict) and isinstance(raw.get("modules"), dict):
        return dict(raw["modules"])
    return {}


def _save_manifest(modules: Dict[str, Any]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, "updated_at": utc_now_iso(), "modules": modules}
    tmp = MANIFEST_PATH.with_name(MANIFEST_PATH.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


# =========================================================================
# Adopt / unlearn
# =========================================================================
def adopt(
    proposal: Proposal,
    *,
    store: Optional[ProposalStore] = None,
    registry: Optional[ToolRegistry] = None,
    replace: bool = False,
) -> Path:
    """Apply an APPROVED proposal: write learned module, record, register."""
    if proposal.status != ProposalStatus.APPROVED.value:
        raise AdoptionError(
            f"Proposal {proposal.id} is {proposal.status}; human approval required first."
        )
    verification = proposal.verification or {}
    if not verification.get("passed"):
        raise AdoptionError(f"Proposal {proposal.id} has no passing verification.")
    target = proposal.target_path
    if target.exists() and not replace:
        raise AdoptionError(
            f"{target.name} already exists — pass replace=True to overwrite it."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(proposal.code, encoding="utf-8")
    os.replace(tmp, target)

    modules = load_manifest()
    modules[proposal.module_name] = {
        "proposal_id": proposal.id,
        "gap_id": proposal.gap_id,
        "gap_name": proposal.gap_name,
        "tool_name": proposal.tool_name,
        "adopted_at": utc_now_iso(),
    }
    _save_manifest(modules)

    if store is not None:
        proposal.status = ProposalStatus.ADOPTED.value
        proposal.adoption = {
            "path": str(target),
            "adopted_at": utc_now_iso(),
            "tool_name": proposal.tool_name,
        }
        store.put(proposal)
    if registry is not None:
        load_learned_tools(registry)
    return target


def unlearn(
    module_name: str,
    *,
    registry: Optional[ToolRegistry] = None,
    store: Optional[ProposalStore] = None,
) -> bool:
    """Remove a learned module + its registered tool. Returns True if removed.

    The owning proposal (if any) goes back to APPROVED so the change can be
    adopted again later — the human consent still stands.
    """
    path = LEARNED_DIR / f"{module_name}.py"
    removed = False
    if path.exists():
        path.unlink()
        removed = True
    sys.modules.pop(f"{MODULE_PACKAGE}.{module_name}", None)
    _LOADED.pop(module_name, None)
    modules = load_manifest()
    if module_name in modules:
        tool_name = str(modules[module_name].get("tool_name") or "")
        modules.pop(module_name, None)
        _save_manifest(modules)
        if registry is not None and tool_name:
            unregister_learned_tool(registry, tool_name)
        removed = True
    if removed:
        proposal_store = store if store is not None else ProposalStore()
        for proposal in proposal_store.list(status=ProposalStatus.ADOPTED.value):
            if proposal.module_name != module_name:
                continue
            proposal.status = ProposalStatus.APPROVED.value
            proposal.adoption = None
            proposal_store.put(proposal)
    return removed


def learned_tool_names() -> List[str]:
    return sorted(str(item.get("tool_name") or "") for item in load_manifest().values())
