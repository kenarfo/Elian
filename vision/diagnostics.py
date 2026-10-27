# ==========================
# ELIAN — vision diagnostics (Phase 5)
# ==========================

from __future__ import annotations

import os
from pathlib import Path


def run_vision_diagnostics(*, save_screenshot: bool = False) -> int:
    print("=== ELIAN VISION DIAGNOSTICS ===")
    print()
    print("No automatic clicking will be performed.")
    print()

    from Config import get_config_value
    from vision.capture import create_default_capture_backend
    from vision.ocr import create_default_ocr
    from vision.provider import create_default_vision_provider
    from vision.session import VisionSession
    from vision.uia import WindowsUIABackend, create_default_ui_backend

    capture = create_default_capture_backend()
    ui = create_default_ui_backend()
    ocr = create_default_ocr()
    vision = create_default_vision_provider()

    title, process = capture.get_active_window_info()
    print(f"Active window: {title or '(none)'}")
    print(f"Process: {process or '(none)'}")
    print(f"Capture backend: {type(capture).__name__}")
    print(f"UIA backend: {type(ui).__name__} available={getattr(ui, 'available', True)}")
    print(f"OCR: {type(ocr).__name__} available={ocr.available}")
    print(
        f"Vision model: {vision.name} available={vision.available} "
        f"(config ai.vision_model={get_config_value('ai','vision_model', default='')!r})"
    )
    print()

    try:
        session = VisionSession(capture=capture, ui=ui, ocr=ocr, vision=vision)
        snap = session.refresh(force=True)
        print(f"Snapshot: {snap.id}")
        print(f"Size: {snap.capture.width}x{snap.capture.height}")
        print(f"Sources: {', '.join(snap.sources)}")
        print(f"Elements: {len(snap.elements)}")
        print(f"Summary: {snap.summary}")
        print()
        print("--- observation ---")
        print(snap.observation_for_planner(max_elements=15))
        print()

        if save_screenshot and snap.capture.image is not None:
            out = Path(os.environ.get("TEMP", ".")) / "elian_vision_diag.png"
            try:
                snap.capture.image.save(out)
                print(f"Diagnostic screenshot saved: {out}")
            except Exception as exc:
                print(f"Could not save screenshot: {exc}")
    except Exception as exc:
        print(f"Diagnostics failed: {exc}")
        return 1

    print("=== END VISION DIAGNOSTICS ===")
    return 0
