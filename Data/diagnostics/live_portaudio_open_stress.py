# ==========================
# Live PortAudio open/close stress (no speech required)
# ==========================
"""Open/close Elian listen path 5x. Expect no -9996/-9985 raise."""

from __future__ import annotations

import time

from Config import get_config_value
import Voice as V
from voice.audio_gate import get_audio_gate, reset_audio_gate_for_tests


def main() -> int:
    reset_audio_gate_for_tests()
    V.initialize_voice()
    name = str(get_config_value("voice", "input_device_name", default="") or "")
    print("config_input_name", name)
    print("resolved_input", V._INPUT_DEVICE, "capture_rate", V._CAPTURE_RATE)
    print("resolved_output", V._OUTPUT_DEVICE)
    for idx, dev, host in V._list_named_devices(name, "input")[:8]:
        print(f"  match #{idx} {dev.get('name')} host={host}")

    errors: list = []
    ok_opens = 0
    for i in range(5):
        t0 = time.perf_counter()
        try:
            audio = V.record_until_silence(wait_timeout=0.35)
            ok_opens += 1
            n = None if audio is None else len(audio)
            snap = get_audio_gate().snapshot()
            print(
                f"cycle {i}: open_ok audio={n} "
                f"wall={time.perf_counter() - t0:.3f}s "
                f"inputs={snap.active_input_streams}"
            )
        except Exception as exc:
            errors.append((i, type(exc).__name__, str(exc)))
            print(f"cycle {i}: FAIL {type(exc).__name__}: {exc}")
            try:
                V.reresolve_input_device(exclude=set())
                print("  reresolved", V._INPUT_DEVICE)
            except Exception as exc2:
                print("  reresolve fail", exc2)

    print("SUMMARY ok_opens", ok_opens, "errors", len(errors))
    for row in errors:
        print(" ERR", row)
    return 0 if not errors and ok_opens == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
