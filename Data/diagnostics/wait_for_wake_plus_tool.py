# ==========================
# Wait until live wake+volume_up succeeds (background-friendly)
# ==========================
"""Loop spoken_wake_rc-style listens until pass_level=WAKE_PLUS_TOOL or max rounds.

Writes Data/diagnostics/WAKE_PLUS_TOOL_PASS.json on success.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIAG = ROOT / "Data" / "diagnostics"
PASS_FILE = DIAG / "WAKE_PLUS_TOOL_PASS.json"


def main() -> int:
    max_rounds = 12
    wait_s = 25
    if "--rounds" in sys.argv:
        i = sys.argv.index("--rounds")
        if i + 1 < len(sys.argv):
            max_rounds = int(sys.argv[i + 1])
    if "--wait" in sys.argv:
        i = sys.argv.index("--wait")
        if i + 1 < len(sys.argv):
            wait_s = float(sys.argv[i + 1])

    script = DIAG / "spoken_wake_rc.py"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("ELIAN_PROTECT_USER_APPS", "1")

    for round_i in range(1, max_rounds + 1):
        print(f"\n=== ROUND {round_i}/{max_rounds} wait={wait_s}s ===\n", flush=True)
        print("POWIEDZ: Hi Elian, volume up", flush=True)
        proc = subprocess.run(
            [sys.executable, str(script), "--wait", str(wait_s)],
            cwd=str(ROOT),
            env=env,
        )
        latest = DIAG / "spoken_wake_rc_latest.json"
        if not latest.is_file():
            continue
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            continue
        level = str(data.get("pass_level") or "")
        status = str(data.get("status") or "")
        print(f"round_result status={status} pass_level={level}", flush=True)
        if status == "PASS" and level == "WAKE_PLUS_TOOL":
            payload = {
                "ok": True,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "round": round_i,
                "evidence": str(latest),
                "stt_text": data.get("stt_text"),
                "followup_stt": data.get("followup_stt"),
                "cm": data.get("cm"),
                "cm_followup": data.get("cm_followup"),
            }
            PASS_FILE.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"PASS wrote {PASS_FILE}", flush=True)
            return 0
        time.sleep(1.0)

    print("FAILED: no WAKE_PLUS_TOOL within rounds", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
