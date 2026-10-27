# ==========================
# ELIAN — doctor / setup check (Phase 9)
# ==========================

from __future__ import annotations

import platform
import sys
from typing import List, Tuple

from Config import config, get_config_value
from core.health import HealthStatus, get_health_registry, reset_health_registry
from core.version import version_banner


def _line(name: str, status: HealthStatus, detail: str = "") -> str:
    label = f"{name:<22}"
    suffix = f"  ({detail})" if detail else ""
    return f"{label}{status.value}{suffix}"


def _check_python() -> Tuple[HealthStatus, str]:
    major, minor = sys.version_info[:2]
    if major >= 3 and minor >= 10:
        return HealthStatus.READY, f"{major}.{minor}"
    return HealthStatus.DEGRADED, f"{major}.{minor} (3.10+ recommended)"


def _check_config() -> Tuple[HealthStatus, str]:
    from Config import validate_config

    issues = validate_config(config)
    if not issues:
        version = get_config_value("settings", "config_version", default=1)
        return HealthStatus.READY, f"v{version}"
    return HealthStatus.DEGRADED, f"{len(issues)} warning(s)"


def _check_voice_stack():
    """Returns voice, wake, stt, tts statuses and details."""
    voice_cfg = config.get("voice") or {}
    wake_enabled = bool(voice_cfg.get("wake_enabled", True))
    tts_enabled = bool(voice_cfg.get("tts_enabled", True))

    wake_status = HealthStatus.READY if wake_enabled else HealthStatus.NOT_CONFIGURED
    wake_detail = "enabled" if wake_enabled else "disabled"

    try:
        from voice.settings import validate_voice_config

        errors = validate_voice_config()
        if errors:
            voice_status = HealthStatus.DEGRADED
            voice_detail = f"{len(errors)} config issue(s)"
        else:
            voice_status = HealthStatus.READY
            voice_detail = "config ok"
    except Exception as exc:
        voice_status = HealthStatus.ERROR
        voice_detail = str(exc)[:60]

    stt_status = HealthStatus.READY
    stt_detail = str(get_config_value("whisper", "device", default="cpu"))
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        stt_status = HealthStatus.UNAVAILABLE
        stt_detail = "faster-whisper not installed"

    tts_status = HealthStatus.READY if tts_enabled else HealthStatus.NOT_CONFIGURED
    return voice_status, voice_detail, wake_status, wake_detail, stt_status, stt_detail, tts_status


def _check_ollama() -> Tuple[HealthStatus, str]:
    if not get_config_value("ai", "agent_enabled", default=True):
        return HealthStatus.NOT_CONFIGURED, "agent disabled"
    try:
        import ollama

        client = ollama.Client(timeout=3.0)
        client.list()
        model = get_config_value("ai", "ollama_model", default="")
        return HealthStatus.READY, model or "default"
    except Exception as exc:
        return HealthStatus.UNAVAILABLE, str(exc)[:80]


def _check_vision() -> Tuple[HealthStatus, str]:
    if not get_config_value("ai", "vision_enabled", default=True):
        return HealthStatus.NOT_CONFIGURED, "disabled"
    model = get_config_value("ai", "vision_model", default="")
    if not model:
        return HealthStatus.DEGRADED, "UIA/OCR only (no vision_model)"
    status, detail = _check_ollama()
    if status == HealthStatus.READY:
        return HealthStatus.READY, model
    return HealthStatus.DEGRADED, f"{model}; ollama: {detail}"


def _check_ha() -> Tuple[HealthStatus, str]:
    if not get_config_value("smart_home", "enabled", default=False):
        return HealthStatus.NOT_CONFIGURED, "smart_home disabled"
    url = get_config_value("smart_home", "url", default="")
    if not url:
        return HealthStatus.NOT_CONFIGURED, "no url"
    return HealthStatus.READY, url[:40]


def _check_integrations() -> List[Tuple[str, HealthStatus, str]]:
    rows = []
    for name, section in (
        ("Calendar", "calendar"),
        ("Reminders", "reminders"),
        ("Media", "media"),
    ):
        enabled = get_config_value("integrations", section, default={})
        if isinstance(enabled, dict):
            on = enabled.get("enabled", True)
        else:
            on = True
        if on:
            rows.append((name, HealthStatus.READY, "enabled"))
        else:
            rows.append((name, HealthStatus.NOT_CONFIGURED, "disabled"))
    return rows


def run_doctor() -> int:
    reset_health_registry()
    reg = get_health_registry()

    print("=== ELIAN DOCTOR ===")
    print(version_banner())
    print(f"Platform: {platform.system()} {platform.release()}")
    print()

    py_status, py_detail = _check_python()
    reg.set("python", py_status, detail=py_detail)
    print(_line("Python", py_status, py_detail))

    cfg_status, cfg_detail = _check_config()
    reg.set("config", cfg_status, detail=cfg_detail)
    print(_line("Config", cfg_status, cfg_detail))

    (
        voice_status,
        voice_detail,
        wake_status,
        wake_detail,
        stt_status,
        stt_detail,
        tts_status,
    ) = _check_voice_stack()
    reg.set("voice", voice_status, detail=voice_detail)
    reg.set("wake", wake_status, detail=wake_detail)
    reg.set("stt", stt_status, detail=stt_detail)
    reg.set("tts", tts_status, detail="enabled" if tts_status == HealthStatus.READY else "disabled")

    print(_line("Voice", voice_status, voice_detail))
    print(_line("Wake", wake_status, wake_detail))
    print(_line("STT", stt_status, stt_detail))
    print(_line("TTS", tts_status))

    ollama_status, ollama_detail = _check_ollama()
    reg.set("ollama", ollama_status, detail=ollama_detail)
    print(_line("Agent/Ollama", ollama_status, ollama_detail))

    vision_status, vision_detail = _check_vision()
    reg.set("vision", vision_status, detail=vision_detail)
    print(_line("Vision", vision_status, vision_detail))

    ha_status, ha_detail = _check_ha()
    reg.set("ha", ha_status, detail=ha_detail)
    print(_line("Home Assistant", ha_status, ha_detail))

    companion_on = bool(get_config_value("companion", "enabled", default=False))
    if companion_on:
        port = get_config_value("companion", "port", default=8742)
        companion_status, companion_detail = HealthStatus.READY, f"port {port}"
    else:
        companion_status, companion_detail = HealthStatus.NOT_CONFIGURED, "disabled (default)"
    reg.set("companion", companion_status, detail=companion_detail)
    print(_line("Companion", companion_status, companion_detail))

    for name, status, detail in _check_integrations():
        key = name.lower()
        reg.set(key, status, detail=detail)
        print(_line(name, status, detail))

    print()
    print(f"Overall: {reg.overall_label()}")
    print("=== END DOCTOR ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_doctor())
