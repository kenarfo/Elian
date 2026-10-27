# ==========================
# ELIAN AI ASSISTANT
# Version 0.6
# Config.py
# ==========================

import json
import os
from copy import deepcopy


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Data")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
SECRETS_FILE = os.path.join(
    DATA_DIR,
    "secrets.json"
)



DEFAULT_CONFIG = {

    "name": "Elian",

    "language": "pl",

    "voice": {
        "model": "pl_PL-darkman-medium",
        "piper_path": "",
        "model_path": "",
        "wake_enabled": True,
        "wake_model": "",
        "wake_threshold": 0.5,
        "wake_cooldown": 1.5,
        "wake_legacy_stt_fallback": True,
        "session_idle_timeout": 90,
        "vad_threshold": 0.5,
        "vad_silence_ms": 700,
        "vad_min_speech_ms": 250,
        "max_recording_seconds": 20,
        "pre_roll_ms": 400,
        "energy_threshold": 0.008,
        "barge_in_enabled": True,
        "barge_in_energy": 0.02,
        "barge_in_grace_ms": 250,
        "echo_guard_ms": 600,
        "ambient_session": True,
        "tts_enabled": True,
        "tts_cache_size": 16,
        "tts_cooldown": 0.35,
        "prefer_fish_tts": True,
        "fish_voice_id": "",
        "fish_s2_exe": r"C:\Users\kenar\ElianResearch\s2.cpp\build\Release\s2.exe",
        "fish_model_dir": r"C:\Users\kenar\ElianResearch\models\s2-pro",
        "fish_quant": "q4_k_m",
        "fish_model_file": "s2-pro-q4_k_m.gguf",
        "fish_tokenizer": "tokenizer.json",
        "fish_voice_profile": "elian_jarvis",
        "fish_voice_dir": r"C:\Users\kenar\ElianResearch\voices",
        "fish_gpu_policy": "exclusive",
        "fish_gpu_device": 0,
        "fish_persistent_worker": True,
        "fish_server_host": "127.0.0.1",
        "fish_server_port": 3037,
        "fish_session_idle_s": 45.0,
        "fish_probe_on_health": False,
        "tts_language": "en-GB",
        "listening_ack": "Słucham.",
        "listening_ack_on_bare_wake": True,
        "auto_unmute_mic": True,
        "input_device": None,
        "input_device_name": "",
        "output_device": None,
        "output_device_name": ""
    },

    "wake_words": [

        "hi elian",
        "hey elian",
        "hej elian",
        "hej eljan",
        "hej ilian",
        "hej adrian",
        "elian",
        "adrian"

    ],

    "personality": {
        "profile": "elian_jarvis",
        "response_style": "concise",
        "formality": "neutral",
        "humor": "low",
        "dry_humor": "low",
        "concise_execution": True,
        "no_service_closing_phrase": True,
        "processing_feedback_after_ms": 1200,
    },

    "ai": {
        "max_same_tool_repeats": 2,
        "max_clarification_rounds": 2,

        "ollama_model": "llama3.1:8b",
        "intent_routing": True,
        "intent_confidence": 0.68,
        "request_timeout": 30,
        "llm_correction": True,
        "agent_enabled": True,
        "agent_max_steps": 6,
        "agent_max_repair": 2,
        "agent_max_replan": 2,
        "agent_plan_timeout_seconds": 45,
        "vision_model": "",
        "vision_enabled": True,
    },

    "whisper": {
        "model": "large-v3-turbo",
        "language": "pl",
        "device": "cuda",
        "compute_type": "float16",
        "fallback_to_cpu": True
    },

    "smart_home": {
        "enabled": False,
        "system": "home_assistant",
        "url": "http://homeassistant.local:8123",
        "entity_id": "input_boolean.lampka_testowa",
        "device_name": "lampa",
        "bluetooth_address": "",
        "refresh_interval": 60,
        "match_cutoff": 0.78,
        "ambiguous_margin": 0.08,
        "color_temperatures": {
            "very_warm": 2200,
            "warm": 2700,
            "neutral": 4000,
            "daylight": 5000,
            "cool": 6500
        },
        "relative_temp_step": 2,
        "relative_brightness_step": 15
    },

    "mqtt_bridge": {
        "enabled": False,
        "host": "homeassistant.local",
        "port": 1883,
        "username": "elian_bridge",
        "bluetooth_address": "",
        "scan_interval": 60,
        "retry_interval": 5
    },

    "computer_control": {
        "enabled": True,
        "search_roots": ["%USERPROFILE%"],
        "search_time_limit": 5,
        "search_file_limit": 100000,
        "confirm_risky_actions": True,
        "computer_action_timeout": 12,
        "computer_observation_timeout": 15
    },

    "screen_perception": {
        "enabled": True,
        "mode": "ACTIVE_LIGHT",
        "provider": "auto",
        "capture_fps": 20,
        "store_frames": False,
        "store_video": False,
        "debug_save_frames": False,
        "change_detection": True,
    },

    "vision": {
        "enabled": True,
        "provider": "ollama",
        "model": "qwen3-vl:8b",
        "fallback_model": None,
        "local_only": True,
        "timeout_seconds": 12,
        "max_concurrent_requests": 1,
        "action_confidence": 0.90,
        "referent_confidence": 0.80,
    },

    "settings": {
        "config_version": 2,
        "remember_user": True,
        "short_answers": True,
        "conversation_idle_timeout": 90,
        "conversation_wait_extension": 60,
        "legacy_skills_startup": True,
        "tts_cooldown": 0.35,
        "context_ttl": 180,
        "cognitive_core_enabled": True,
    },

    "web_research": {
        "enabled": True,
        "timeout": 12,
        "max_pages": 3,
        "max_chars_per_page": 8000
    },

    "web": {
        "enabled": True,
        "search_provider": "duckduckgo",
        "api_key": "",
        "max_results": 5,
        "fetch_enabled": True
    },

    "proactive": {
        "enabled": True,
        "system_poll_seconds": 10,
        "ha_poll_seconds": 60,
        "battery_thresholds": [20, 10, 5],
        "battery_critical_percent": 5,
        "cpu_threshold": 95,
        "cpu_duration_seconds": 120,
        "ram_threshold": 92,
        "ram_duration_seconds": 120,
        "disk_free_gb": 10,
        "network_debounce_seconds": 15,
        "queue_limit": 8,
        "max_per_window": 3,
        "rate_window_seconds": 600,
        "battery_enabled": True,
        "system_enabled": True,
        "home_assistant_enabled": True,
        "network_enabled": True,
        "os_notifications": {
            "enabled": False,
            "poll_seconds": 8
        },
        "quiet_hours": {
            "enabled": False,
            "start": "22:00",
            "end": "07:00"
        },
        "cooldowns": {
            "cpu_high_sustained": 1800,
            "ram_high_sustained": 1800,
            "disk_low": 86400,
            "ha_unavailable": 1800
        }
    },

    "integrations": {
        "calendar": {
            "enabled": True,
            "provider": "local",
            "poll_seconds": 60,
            "upcoming_minutes": 15
        },
        "reminders": {
            "enabled": True,
            "poll_seconds": 15,
            "grace_seconds": 3600
        },
        "media": {
            "enabled": True,
            "default_app": "spotify"
        },
        "messaging": {
            "enabled": True
        },
        "home_assistant": {
            "enabled": True
        },
        "contacts": {
            "enabled": True
        },
        "twilio": {
            "enabled": False,
            "from_number": "",
            "poll_seconds": 30
        },
        "browser": {
            "enabled": False,
            "headless": True,
            "max_steps": 12,
            "max_wall_seconds": 45,
            "action_timeout_sec": 12
        }
    },

    "hud": {
        "enabled": False,
        "always_on_top": True,
        "compact": False,
        "mini": False,
        "hide_to_tray": True,
        "theme": "dark",
        "opacity": 0.94,
        "animations": True,
        "dev_view": False,
        "proactive_enabled": True,
        "world_update_ms": 500
    },

    "companion": {
        "enabled": False,
        "bind": "0.0.0.0",
        "port": 8742,
        "pairing_window_seconds": 120,
        "heartbeat_timeout_seconds": 45,
        "rate_limit_per_minute": 60,
        "allow_audio_upload": True,
        "lan_only": True,
        "also_speak": False,
        "max_queued_events": 50,
        "command_timeout_seconds": 25,
        "max_command_chars": 500,
        "request_cache_ttl_seconds": 300
    }

}



def create_config():

    if not os.path.exists(DATA_DIR):

        os.makedirs(DATA_DIR)


    if not os.path.exists(CONFIG_FILE):

        with open(
            CONFIG_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                DEFAULT_CONFIG,
                file,
                ensure_ascii=False,
                indent=4
            )



def merge_config(defaults, values):

    result = deepcopy(defaults)

    for key, value in values.items():

        if (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):

            result[key] = merge_config(
                result[key],
                value
            )

        else:

            result[key] = value

    return result


def load_config():

    create_config()


    try:

        with open(
            CONFIG_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            loaded = json.load(file)

            if not isinstance(loaded, dict):

                return deepcopy(DEFAULT_CONFIG)

            return merge_config(
                DEFAULT_CONFIG,
                loaded
            )


    except (OSError, json.JSONDecodeError):

        return deepcopy(DEFAULT_CONFIG)


def get_config_value(
    section,
    key,
    default=None,
    legacy_key=None
):

    section_config = config.get(
        section,
        {}
    )

    if (
        isinstance(section_config, dict)
        and key in section_config
    ):

        return section_config[key]

    if (
        legacy_key
        and legacy_key in config
    ):

        return config[legacy_key]

    return default


def load_secrets():

    try:

        with open(
            SECRETS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            loaded = json.load(file)

            if isinstance(loaded, dict):

                return loaded

    except (
        OSError,
        json.JSONDecodeError
    ):

        pass

    return {}


def get_secret(key, env_var=None):

    if env_var:

        value = os.getenv(
            env_var,
            ""
        ).strip()

        if value:

            return value

    value = secrets.get(
        key,
        ""
    )

    if isinstance(value, str):

        return value.strip()

    return ""



def _flatten_keys(data, prefix=""):
    keys = set()
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            keys.add(path)
            keys |= _flatten_keys(value, path)
    return keys


def _default_keys():
    return _flatten_keys(DEFAULT_CONFIG)


def validate_config(cfg=None):
    """Return list of validation warnings (non-fatal)."""
    cfg = cfg if cfg is not None else config
    warnings = []

    known = _default_keys()
    for key in _flatten_keys(cfg):
        top = key.split(".", 1)[0]
        if key not in known and top not in DEFAULT_CONFIG:
            warnings.append(f"Unknown config key: {key}")

    voice = cfg.get("voice") or {}
    threshold = voice.get("wake_threshold")
    if threshold is not None:
        try:
            t = float(threshold)
            if t < 0 or t > 1:
                warnings.append(
                    f"voice.wake_threshold out of range (0-1): {threshold}"
                )
        except (TypeError, ValueError):
            warnings.append(f"voice.wake_threshold invalid: {threshold}")

    ai = cfg.get("ai") or {}
    for key, lo, hi in (
        ("agent_max_steps", 1, 20),
        ("agent_max_repair", 1, 10),
        ("agent_max_replan", 0, 10),
        ("max_same_tool_repeats", 1, 10),
        ("request_timeout", 1, 600),
    ):
        val = ai.get(key)
        if val is not None:
            try:
                n = int(val)
                if n < lo or n > hi:
                    warnings.append(f"ai.{key} out of range ({lo}-{hi}): {n}")
            except (TypeError, ValueError):
                warnings.append(f"ai.{key} invalid: {val}")

    return warnings


def migrate_config(cfg):
    """Apply in-memory migrations for older config_version values."""
    settings = cfg.setdefault("settings", {})
    version = int(settings.get("config_version") or 1)
    if version < 2:
        settings["config_version"] = 2
    return cfg



config = migrate_config(load_config())

secrets = load_secrets()

# When launched via unittest/pytest, keep product config intact but prevent
# background self-dev mutations and LLM streaming that bypass ask_ai patches.
import sys as _sys

_argv = " ".join(_sys.argv).lower()
if "unittest" in _argv or "pytest" in _argv:
    os.environ.setdefault("ELIAN_DISABLE_BACKGROUND_SELF_DEV", "1")
    os.environ.setdefault("ELIAN_STREAM_LLM", "0")
