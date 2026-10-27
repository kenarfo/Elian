# ==========================
# ELIAN — RC wake policy (Phase 9.5)
# ==========================
# In RC Validation: never use developer OpenWakeWord defaults for "Elian".
# Missing custom elian*.onnx → Legacy STT wake (Whisper + WakeWord text).

from __future__ import annotations

from typing import Callable, Optional, Tuple

from voice.settings import VoiceSettings
from voice.wake import (
    LegacySttWakeListener,
    WakeListenerBase,
    create_wake_listener,
    has_custom_elian_wake_model,
)


ACOUSTIC_STATUS_MISSING = "MANUAL TEST REQUIRED / CUSTOM MODEL MISSING"
ACOUSTIC_STATUS_READY = "READY"

# Re-export for RC collectors / tests that import from this module.
__all__ = (
    "ACOUSTIC_STATUS_MISSING",
    "ACOUSTIC_STATUS_READY",
    "has_custom_elian_wake_model",
    "print_rc_wake_banner",
    "resolve_rc_wake_listener",
)


def resolve_rc_wake_listener(
    settings: VoiceSettings,
    *,
    listen_fn: Callable[..., str],
    state_machine=None,
    is_speaking: Optional[Callable[[], bool]] = None,
) -> Tuple[WakeListenerBase, str, dict]:
    """
    Choose wake backend for RC Validation only.

    Returns (listener, mode, meta) where meta includes acoustic_status for reports.
    """
    model_path = str(settings.wake_model or "")
    custom = has_custom_elian_wake_model(model_path)

    if custom:
        listener, mode = create_wake_listener(
            settings,
            state_machine=state_machine,
            is_speaking=is_speaking,
            listen_fn=listen_fn,
        )
        return listener, mode, {
            "custom_elian_onnx_installed": True,
            "wake_model_path": model_path,
            "acoustic_wake_status": (
                ACOUSTIC_STATUS_READY
                if mode == "acoustic"
                else ACOUSTIC_STATUS_MISSING
            ),
            "forced_legacy_stt": False,
        }

    # Missing custom Elian model — do NOT use OpenWakeWord developer defaults.
    if listen_fn is None:
        raise RuntimeError("listen_fn required for RC legacy STT wake")

    listener = LegacySttWakeListener(listen_fn, settings)
    return listener, "legacy_stt", {
        "custom_elian_onnx_installed": False,
        "wake_model_path": model_path,
        "acoustic_wake_status": ACOUSTIC_STATUS_MISSING,
        "forced_legacy_stt": True,
        "warning": "CUSTOM ELIAN WAKE MODEL NOT INSTALLED",
        "wake_mode_label": "LEGACY STT FALLBACK",
    }


def print_rc_wake_banner(meta: dict) -> None:
    if meta.get("forced_legacy_stt") or not meta.get("custom_elian_onnx_installed"):
        print()
        print("⚠ CUSTOM ELIAN WAKE MODEL NOT INSTALLED")
        print("WAKE MODE: LEGACY STT FALLBACK")
        print()
    elif meta.get("wake_model_path"):
        print(f"[RC] Custom wake model: {meta['wake_model_path']}")
        print(f"[RC] WAKE MODE: {meta.get('mode', 'acoustic').upper()}")
        print()
