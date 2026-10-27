# Perception

Keep the current ScreenPerception path. Do not add a second vision loop.

## Inputs (existing)

- Foreground HWND chain: ctypes_user32 → pywin32 → windows_observation → uia_active
- WinEvent hook with 8 Hz poll fallback (`core/perception/foreground.py`)
- UIA + optional pixels (`core/perception/*`, `vision/`)
- Visual referents (`visual_referent.py`)

## Rules

- “Co widzisz?” / “co mam otwarte?” force current observation (`requires_fresh_observation`).
- UIA/DOM wins for exact controls. Vision is fallback.
- Never persist raw frames into WorldState.
- Low-confidence visual “ten po prawej” → ASK. Never click randomly.
- Interactive `--foreground-smoke` must start the watcher (not a one-shot read).

Camera stays off. No silent capture.
