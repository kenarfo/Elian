# ==========================
# ELIAN — Windows capture mute helpers (pycaw)
# ==========================
"""Detect / clear OS microphone mute so wake/STT is not silently deaf.

Default capture endpoint mute is a common Windows state after calls / privacy UI.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple


def get_capture_mute_state() -> Tuple[Optional[bool], str]:
    """Return (muted, detail). muted is None when the API is unavailable."""
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except Exception as exc:
        return None, f"pycaw_unavailable:{type(exc).__name__}"

    try:
        device = AudioUtilities.GetMicrophone()
        if device is None:
            return None, "no_microphone_endpoint"
        iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = iface.QueryInterface(IAudioEndpointVolume)
        muted = bool(vol.GetMute())
        level = float(vol.GetMasterVolumeLevelScalar())
        return muted, f"muted={int(muted)} level={level:.3f}"
    except Exception as exc:
        return None, f"query_failed:{type(exc).__name__}:{exc}"


def set_capture_mute(muted: bool) -> Tuple[bool, str]:
    """Set OS microphone mute. Returns (ok, detail)."""
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    except Exception as exc:
        return False, f"pycaw_unavailable:{type(exc).__name__}"

    try:
        device = AudioUtilities.GetMicrophone()
        if device is None:
            return False, "no_microphone_endpoint"
        iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = iface.QueryInterface(IAudioEndpointVolume)
        vol.SetMute(1 if muted else 0, None)
        return True, f"set_mute={int(bool(muted))}"
    except Exception as exc:
        return False, f"set_failed:{type(exc).__name__}:{exc}"


def ensure_capture_unmuted(*, auto_unmute: bool = True) -> dict[str, Any]:
    """If the default capture endpoint is muted, optionally unmute it.

    Returns a small status dict for logs / diagnostics.
    """
    muted, detail = get_capture_mute_state()
    out: dict[str, Any] = {
        "muted": muted,
        "detail": detail,
        "unmuted": False,
        "auto_unmute": bool(auto_unmute),
    }
    if muted is not True:
        return out
    print(
        "[AUDIO] Mikrofon Windows jest wyciszony (mute) — "
        "wake/STT nie usłyszy mowy."
    )
    if not auto_unmute:
        out["action"] = "warn_only"
        return out
    ok, set_detail = set_capture_mute(False)
    out["unmuted"] = bool(ok)
    out["set_detail"] = set_detail
    if ok:
        print("[AUDIO] Włączono mikrofon (auto_unmute_mic).")
        muted2, detail2 = get_capture_mute_state()
        out["muted_after"] = muted2
        out["detail_after"] = detail2
    else:
        print(f"[AUDIO] Nie udało się wyłączyć mute: {set_detail}")
    return out
