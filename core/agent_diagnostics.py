# ==========================
# ELIAN — agent parse diagnostics (Phase 10.1)
# ==========================

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import Any, Optional

from Config import get_config_value


@dataclass
class ParseDiagnostics:
    response_kind: str = "empty"
    json_parse_ok: bool = False
    schema_validation_ok: bool = False
    decision_type: str = ""
    tool_name: str = ""
    argument_keys: str = ""
    parse_error_type: str = ""
    validation_error: str = ""
    retry_reason: str = ""


def _store_raw_responses() -> bool:
    return bool(get_config_value("diagnostics", "store_transcripts", default=False))


def log_agent_raw(
    diag: ParseDiagnostics,
    *,
    attempt: int = 0,
    raw_preview: str = "",
) -> None:
    parts = [
        "[AGENT_RAW]",
        f"attempt={attempt}",
        f"response_kind={diag.response_kind}",
        f"json_parse_ok={diag.json_parse_ok}",
        f"schema_validation_ok={diag.schema_validation_ok}",
        f"decision_type={diag.decision_type or '-'}",
        f"tool_name={diag.tool_name or '-'}",
        f"argument_keys={diag.argument_keys or '-'}",
        f"parse_error_type={diag.parse_error_type or '-'}",
        f"validation_error={diag.validation_error or '-'}",
        f"retry_reason={diag.retry_reason or '-'}",
    ]
    print(" ".join(parts))
    if _store_raw_responses() and raw_preview:
        preview = raw_preview[:240].replace("\n", " ")
        print(f"[AGENT_RAW] preview={preview}")


def diagnostics_to_dict(diag: ParseDiagnostics) -> dict[str, Any]:
    return asdict(diag)
