# ==========================
# ELIAN AI ASSISTANT
# Voice.py — compatibility facade + STT/TTS (Phase 4)
# ==========================

import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel
from collections import deque, OrderedDict
from queue import Empty, Queue
import subprocess
import soundfile as sf
import os
import shutil
import torch
import uuid
import time
import threading
from silero_vad import load_silero_vad

from Config import config, get_config_value
from TextCorrector import apply_known_corrections, correct_text
from core.latency import current_tracker
from voice.audio_gate import get_audio_gate
from voice.settings import VoiceSettings


# ==========================
# WHISPER
# ==========================

WHISPER_MODEL = get_config_value(
    "whisper",
    "model",
    default="large-v3-turbo",
    legacy_key="whisper_model"
)

WHISPER_DEVICE = os.getenv(
    "ELIAN_WHISPER_DEVICE",
    get_config_value(
        "whisper",
        "device",
        default="cuda"
    )
)

WHISPER_COMPUTE_TYPE = os.getenv(
    "ELIAN_WHISPER_COMPUTE_TYPE",
    get_config_value(
        "whisper",
        "compute_type",
        default="float16"
    )
)

WHISPER_LANGUAGE = get_config_value(
    "whisper",
    "language",
    default="pl"
)

WHISPER_FALLBACK_TO_CPU = bool(
    get_config_value(
        "whisper",
        "fallback_to_cpu",
        default=True
    )
)

whisper = None
vad_model = None

_voice_settings = None
_speaking_lock = threading.Lock()
_is_speaking = False
_last_spoken_text = ""
_stop_playback = threading.Event()
_tts_cache: OrderedDict[str, tuple] = OrderedDict()
_INPUT_DEVICE = None
_OUTPUT_DEVICE = None
# Mic open rate (may be device-native); STT/VAD always see RATE (16 kHz).
_CAPTURE_RATE = 16000
# Stream TTS (Phase 0–2): cancel Piper + drain queues without touching mic architecture.
_stream_tts_cancel = threading.Event()
_piper_lock = threading.Lock()
_active_piper_proc = None
_spoken_stream_parts: list[str] = []


def get_voice_settings() -> VoiceSettings:
    global _voice_settings
    if _voice_settings is None:
        _voice_settings = VoiceSettings.from_config()
    return _voice_settings


def refresh_voice_settings() -> VoiceSettings:
    global _voice_settings, RATE, BLOCK, SILENCE_LIMIT, MAX_RECORDING
    global ENERGY_THRESHOLD, VAD_THRESHOLD, TTS_COOLDOWN
    _voice_settings = VoiceSettings.from_config()
    vs = _voice_settings
    RATE = vs.sample_rate
    BLOCK = vs.block_size
    SILENCE_LIMIT = vs.vad_silence_ms / 1000.0
    MAX_RECORDING = vs.max_recording_seconds
    ENERGY_THRESHOLD = vs.energy_threshold
    VAD_THRESHOLD = vs.vad_threshold
    TTS_COOLDOWN = vs.tts_cooldown
    return vs


def _is_virtual_capture_name(name: str) -> bool:
    low = str(name or "").lower()
    return "easeus" in low or "voicewave" in low


def get_whisper_model():
    global whisper

    if whisper is not None:
        return whisper

    print("Ładowanie Whisper...")

    try:
        whisper = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE
        )
        print(f"[STT] device={WHISPER_DEVICE} model={WHISPER_MODEL}")
    except Exception as error:
        if WHISPER_DEVICE != "cuda" or not WHISPER_FALLBACK_TO_CPU:
            raise

        print("⚠️ Whisper GPU niedostępny:", error)
        print("Uruchamiam Whisper na CPU.")
        whisper = WhisperModel(
            WHISPER_MODEL,
            device="cpu",
            compute_type="int8"
        )
        print("[STT] device=cpu (fallback)")

    print("Whisper gotowy.")
    return whisper


def get_vad_model():
    global vad_model

    if vad_model is None:
        print("Ładowanie Silero VAD...")
        vad_model = load_silero_vad()
        print("Silero VAD gotowy.")

    return vad_model


def _host_preference(kind: str, host: str) -> int:
    """Lower = better. WASAPI is last for output (rejects Piper 22050 on headsets)."""
    h = (host or "").upper()
    if kind == "output":
        order = ("DIRECTSOUND", "MME", "WDM-KS", "WASAPI")
    else:
        # Input: WDM-KS gives real Plantronics levels; MME/DS often near-silent
        # at forced 16 kHz; WASAPI rejects 16 kHz on Blackwire.
        order = ("WDM-KS", "MME", "DIRECTSOUND", "WASAPI")
    for i, token in enumerate(order):
        if token in h:
            return i
    return len(order)


def _input_name_candidates(name: str) -> list:
    """Config substring plus aliases (Windows often truncates MME brand names)."""
    raw = str(name or "").strip()
    if not raw:
        return []
    out = [raw]
    low = raw.lower()
    if "plantronics" in low or "blackwire" in low:
        for alias in ("Plantronics", "Blackwire"):
            if alias.lower() not in {a.lower() for a in out}:
                out.append(alias)
    return out


def _list_named_devices(name: str, kind: str) -> list:
    """All PortAudio indices matching name + direction, with host labels."""
    if not name:
        return []
    try:
        devices = sd.query_devices()
    except Exception as exc:
        print(f"[AUDIO] Cannot query devices: {exc}")
        return []

    needles = [n.lower() for n in _input_name_candidates(name)] if kind == "input" else [
        name.lower()
    ]
    matches = []
    seen = set()
    for idx, dev in enumerate(devices):
        dev_name = str(dev.get("name", ""))
        if _is_virtual_capture_name(dev_name):
            continue
        low = dev_name.lower()
        if not any(n in low for n in needles):
            continue
        if kind == "input" and int(dev.get("max_input_channels", 0)) <= 0:
            continue
        if kind == "output" and int(dev.get("max_output_channels", 0)) <= 0:
            continue
        if idx in seen:
            continue
        seen.add(idx)
        host = ""
        try:
            host = str(sd.query_hostapis(dev["hostapi"])["name"])
        except Exception:
            pass
        matches.append((idx, dev, host))
    matches.sort(key=lambda m: _host_preference(kind, m[2]))
    return matches


def _choose_capture_rate(device_idx, target_rate: int) -> int:
    """Use STT rate when the host accepts it; else device-native (resample later)."""
    target = int(target_rate) if target_rate else 16000
    if device_idx is None:
        return target
    try:
        sd.check_input_settings(
            device=device_idx, samplerate=target, channels=1
        )
        return target
    except Exception:
        pass
    try:
        native = int(sd.query_devices(device_idx)["default_samplerate"])
        if native > 0:
            return native
    except Exception:
        pass
    return 48000


def _resolve_device(name: str, kind: str, samplerate: int | None = None):
    """Resolve device by substring name; None = default.

    Prefer a host API that accepts ``samplerate`` when given (WASAPI often
    rejects 16 kHz mic / 22.05 kHz Piper on Plantronics). For output, prefer
    DirectSound/MME over WASAPI even when falling back.
    """
    matches = _list_named_devices(name, kind)
    if not matches:
        if name:
            print(f"[AUDIO] Device name '{name}' not found — using default {kind}")
        return None

    chosen = None
    if samplerate:
        channels = 1
        for idx, dev, host in matches:
            try:
                if kind == "input":
                    sd.check_input_settings(
                        device=idx, samplerate=int(samplerate), channels=channels
                    )
                else:
                    sd.check_output_settings(
                        device=idx, samplerate=int(samplerate), channels=channels
                    )
                chosen = (idx, dev, host)
                break
            except Exception:
                continue
    if chosen is None and kind == "input" and samplerate:
        # Fall back to a host that opens at native rate (WASAPI Plantronics).
        for idx, dev, host in matches:
            try:
                native = int(dev.get("default_samplerate") or 0) or 48000
                sd.check_input_settings(
                    device=idx, samplerate=native, channels=1
                )
                chosen = (idx, dev, host)
                print(
                    f"[AUDIO] input will capture @{native} Hz then resample "
                    f"to {int(samplerate)} (host rejects STT rate)"
                )
                break
            except Exception:
                continue
    if chosen is None:
        # Prefer rate-flexible hosts (already sorted); never pick WASAPI first.
        chosen = matches[0]
    idx, dev, host = chosen
    print(f"[AUDIO] {kind}: {dev.get('name')} (#{idx}, {host or 'host?'})")
    return idx


def _is_portaudio_device_error(exc: BaseException) -> bool:
    """True for stale/busy/host failures that warrant mic re-bind (-9996/-9985/-9999)."""
    msg = str(exc).lower()
    tokens = (
        "-9996",
        "-9985",
        "-9999",
        "invalid device",
        "device unavailable",
        "error opening inputstream",
        "unanticipated host error",
        "blocking api not supported",
    )
    return any(t in msg for t in tokens)


def reresolve_input_device(*, exclude: set | None = None) -> int | None:
    """Re-bind capture device by configured name (stale PortAudio index → -9996).

    Updates ``_INPUT_DEVICE`` / ``_CAPTURE_RATE`` and voice settings so wake,
    listen, and barge share the same binding after USB/host reorder.
    """
    global _INPUT_DEVICE, _CAPTURE_RATE, _voice_settings
    from dataclasses import replace

    vs = get_voice_settings()
    name = str(getattr(vs, "input_device_name", "") or "").strip()
    if not name:
        print("[AUDIO] Re-resolve skipped — no voice.input_device_name")
        return None

    skipped = {int(x) for x in (exclude or set()) if x is not None}
    matches = _list_named_devices(name, "input")
    for idx, dev, host in matches:
        if int(idx) in skipped:
            continue
        try:
            sd.query_devices(int(idx))
        except Exception:
            continue
        _INPUT_DEVICE = int(idx)
        _CAPTURE_RATE = _choose_capture_rate(_INPUT_DEVICE, int(vs.sample_rate))
        _voice_settings = replace(
            vs,
            input_device=_INPUT_DEVICE,
            output_device=_OUTPUT_DEVICE,
        )
        print(
            f"[AUDIO] Re-resolved input #{_INPUT_DEVICE}: "
            f"{dev.get('name')} ({host or 'host?'}, "
            f"capture={_CAPTURE_RATE} Hz)"
        )
        return _INPUT_DEVICE
    print(f"[AUDIO] Re-resolve failed — no usable host for '{name}'")
    return None


def resolve_barge_input_device(preferred_index=None, name: str = "") -> int | None:
    """Input device for barge monitor — must support Blocking InputStream.

    WDM-KS on Windows often fails with PaError -9999 ('Blocking API not supported').
    Prefer MME / DirectSound for the same logical mic; fall back to preferred.
    """
    try:
        import sounddevice as sd
    except Exception:
        return preferred_index

    matches: list = []
    if name:
        matches = _list_named_devices(name, "input")
    if not matches and preferred_index is not None:
        try:
            dev = sd.query_devices(int(preferred_index))
            host = ""
            try:
                host = str(sd.query_hostapis(dev["hostapi"])["name"])
            except Exception:
                pass
            matches = [(int(preferred_index), dev, host)]
            # Also collect siblings by name.
            siblings = _list_named_devices(str(dev.get("name") or ""), "input")
            for row in siblings:
                if row[0] not in {m[0] for m in matches}:
                    matches.append(row)
        except Exception:
            pass

    if not matches:
        return preferred_index

    # Prefer hosts known to support Blocking API for barge energy monitor.
    prefer = ("MME", "DIRECTSOUND", "WINDOWS WASAPI", "WASAPI")
    ranked = sorted(
        matches,
        key=lambda row: (
            0
            if any(token in str(row[2] or "").upper() for token in prefer)
            and "WDM" not in str(row[2] or "").upper()
            else 1,
            _host_preference("input", str(row[2] or "")),
        ),
    )
    for idx, dev, host in ranked:
        host_u = str(host or "").upper()
        if "WDM" in host_u:
            continue
        try:
            # Probe Blocking open briefly — avoid using a host that will -9999.
            with sd.InputStream(
                samplerate=int(dev.get("default_samplerate") or 16000),
                channels=1,
                dtype="float32",
                blocksize=512,
                device=idx,
            ):
                pass
            if preferred_index is None or idx != preferred_index:
                print(
                    f"[AUDIO] barge input: {dev.get('name')} (#{idx}, {host or 'host?'})"
                )
            return idx
        except Exception:
            continue
    # Last resort: preferred index (may still fail — caller degrades).
    return preferred_index


def _resample_audio(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear resample (mono or multi-channel). Same approach as companion STT."""
    src = int(src_rate)
    dst = int(dst_rate)
    if src <= 0 or dst <= 0 or src == dst:
        return audio
    if audio is None or len(audio) == 0:
        return audio
    if getattr(audio, "ndim", 1) > 1:
        cols = [
            _resample_audio(audio[:, c], src, dst) for c in range(audio.shape[1])
        ]
        return np.column_stack(cols).astype(np.float32)
    duration = float(len(audio)) / float(src)
    target_n = max(1, int(round(duration * dst)))
    x_old = np.linspace(0.0, 1.0, len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, target_n, endpoint=False)
    return np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)


def _fit_playback_rate(
    audio: np.ndarray, rate: int, device, *, force_rate: int | None = None
) -> tuple:
    """Resample TTS to a rate the output device accepts (WASAPI is picky)."""
    rate = int(rate)
    channels = 1 if getattr(audio, "ndim", 1) == 1 else int(audio.shape[1])
    # Normalize to float32 mono-friendly buffer for PortAudio.
    if getattr(audio, "dtype", None) is not None and audio.dtype != np.float32:
        audio = audio.astype(np.float32)

    if force_rate is not None:
        force_rate = int(force_rate)
        if force_rate != rate:
            print(f"[TTS] force-resample {rate} -> {force_rate} Hz")
            return _resample_audio(audio, rate, force_rate), force_rate
        return audio, rate

    def _ok(sr: int) -> bool:
        try:
            sd.check_output_settings(
                device=device, samplerate=int(sr), channels=channels
            )
            return True
        except Exception:
            return False

    if _ok(rate):
        return audio, rate

    candidates = []
    try:
        if device is not None:
            candidates.append(int(sd.query_devices(device)["default_samplerate"]))
    except Exception:
        pass
    candidates.extend([48000, 44100, 22050, 16000])

    seen = set()
    for cand in candidates:
        cand = int(cand)
        if cand in seen or cand == rate:
            continue
        seen.add(cand)
        if not _ok(cand):
            continue
        print(f"[TTS] resampling {rate} -> {cand} Hz for output device")
        return _resample_audio(audio, rate, cand), cand

    # Last resort: resample to 48 kHz even if check failed (some hosts lie).
    force = 48000
    if rate != force:
        print(f"[TTS] force-resample {rate} -> {force} Hz")
        return _resample_audio(audio, rate, force), force
    return audio, rate


def _is_invalid_sample_rate(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "invalid sample rate" in msg or "-9997" in msg


def _output_device_candidates() -> list:
    """Configured output first, then other Plantronics/name matches, then default."""
    vs = get_voice_settings()
    ordered = []
    seen = set()

    def _add(idx):
        if idx is None:
            return
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            return
        if idx in seen:
            return
        seen.add(idx)
        ordered.append(idx)

    _add(_OUTPUT_DEVICE)
    name = ""
    try:
        name = str(vs.output_device_name or "")
    except Exception:
        name = ""
    if name:
        for idx, _dev, _host in _list_named_devices(name, "output"):
            _add(idx)
    # Shorter match if config uses a long product string.
    if "plantronics" in name.lower():
        for idx, _dev, _host in _list_named_devices("Plantronics", "output"):
            _add(idx)
    ordered.append(None)  # Windows default
    return ordered


def _device_index(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return _device_index(value[0] if value else None)
    try:
        idx = int(value)
        return idx if idx >= 0 else None
    except (TypeError, ValueError):
        try:
            return _device_index(value[0])
        except Exception:
            return None


def _log_default_devices():
    try:
        default = sd.default.device
        devices = sd.query_devices()
        try:
            inp = _device_index(default[0])
            out = _device_index(default[1])
        except Exception:
            inp = out = _device_index(default)
        in_name = devices[inp]["name"] if inp is not None else "default"
        out_name = devices[out]["name"] if out is not None else "default"
        print(f"[AUDIO] Input: {in_name}")
        print(f"[AUDIO] Output: {out_name}")
    except Exception as exc:
        print(f"[AUDIO] Device info unavailable: {exc}")


def initialize_voice():
    global _INPUT_DEVICE, _OUTPUT_DEVICE, _CAPTURE_RATE, _voice_settings
    from dataclasses import replace

    vs = refresh_voice_settings()
    _INPUT_DEVICE = vs.input_device
    if _INPUT_DEVICE is None and vs.input_device_name:
        # Prefer host that accepts STT rate (16 kHz); WASAPI LCS often rejects it.
        _INPUT_DEVICE = _resolve_device(
            vs.input_device_name, "input", samplerate=int(vs.sample_rate)
        )
    _OUTPUT_DEVICE = vs.output_device
    if _OUTPUT_DEVICE is None and vs.output_device_name:
        # Piper is typically 22050 Hz; pick a host that accepts it when possible.
        _OUTPUT_DEVICE = _resolve_device(
            vs.output_device_name, "output", samplerate=22050
        )
    _CAPTURE_RATE = _choose_capture_rate(_INPUT_DEVICE, int(vs.sample_rate))
    # Wake/barge/pipeline must use the same resolved indices (not Windows default).
    _voice_settings = replace(
        vs,
        input_device=_INPUT_DEVICE,
        output_device=_OUTPUT_DEVICE,
    )
    _log_default_devices()
    try:
        devices = sd.query_devices()
        if _INPUT_DEVICE is not None:
            print(
                f"[AUDIO] Using input #{_INPUT_DEVICE}: "
                f"{devices[_INPUT_DEVICE]['name']} "
                f"(capture={_CAPTURE_RATE} Hz → stt={int(vs.sample_rate)} Hz)"
            )
        else:
            print(
                "[AUDIO] Using Windows default INPUT — prefer headset in config "
                f"(capture={_CAPTURE_RATE} Hz)"
            )
        if _OUTPUT_DEVICE is not None:
            print(f"[AUDIO] Using output #{_OUTPUT_DEVICE}: {devices[_OUTPUT_DEVICE]['name']}")
        # Decision note for mic conflicts (LCS default vs Plantronics headset).
        try:
            in_name = (
                devices[_INPUT_DEVICE]["name"]
                if _INPUT_DEVICE is not None
                else devices[sd.default.device[0]]["name"]
            )
            if "plantronics" in str(in_name).lower() or "blackwire" in str(in_name).lower():
                print(
                    "[AUDIO] Mic choice: Plantronics Blackwire (close-talk headset). "
                    "LCS_USB_Audio is Windows default/room mic — not used for STT."
                )
            elif "lcs" in str(in_name).lower():
                print(
                    "[AUDIO] Mic choice: LCS_USB_Audio. For headset RC set "
                    "voice.input_device_name to Plantronics Blackwire."
                )
        except Exception:
            pass
    except Exception:
        pass
    try:
        from voice.mic_mute import ensure_capture_unmuted

        auto = bool(get_config_value("voice", "auto_unmute_mic", default=True))
        ensure_capture_unmuted(auto_unmute=auto)
    except Exception as exc:
        print(f"[AUDIO] mic mute check skipped: {exc}")
    get_vad_model()
    get_whisper_model()


def detect_speech(audio):
    samples = audio.flatten().astype(np.float32)
    if len(samples) == 0:
        return False

    tensor = torch.from_numpy(samples)
    with torch.no_grad():
        probability = get_vad_model()(tensor, RATE).item()
    return probability >= VAD_THRESHOLD


def _speech_gate(audio, volume: float) -> bool:
    """Silero VAD, with loud-energy fallback when VAD is over-rejecting."""
    if volume <= ENERGY_THRESHOLD:
        return False
    try:
        if detect_speech(audio):
            return True
    except Exception:
        pass
    # Close-talk headset often peaks ~0.015–0.022 while Silero rejects short
    # bursts. Old floor max(0.018, thr*3)=0.024 missed real speech (Plantronics).
    # Still above room bleed (~0.003–0.008).
    return volume >= max(0.012, ENERGY_THRESHOLD * 2.0)


# ==========================
# PIPER
# ==========================

VOICE_CONFIG = config.get("voice", {})
if not isinstance(VOICE_CONFIG, dict):
    VOICE_CONFIG = {}

PIPER = os.getenv(
    "ELIAN_PIPER_PATH",
    VOICE_CONFIG.get("piper_path", "")
)

VOICE_MODEL = os.getenv(
    "ELIAN_PIPER_MODEL_PATH",
    VOICE_CONFIG.get("model_path", "")
)


# ==========================
# AUDIO (defaults; refreshed from VoiceSettings)
# ==========================

RATE = 16000
CHANNELS = 1
BLOCK = 512
SILENCE_LIMIT = 0.7
MAX_RECORDING = 20
ENERGY_THRESHOLD = 0.008
VAD_THRESHOLD = 0.5
TTS_COOLDOWN = float(
    get_config_value("settings", "tts_cooldown", default=0.35)
)


def is_speaking() -> bool:
    with _speaking_lock:
        return _is_speaking


def last_spoken_text() -> str:
    return _last_spoken_text


def request_playback_interrupt() -> None:
    """Signal TTS waiter to stop. Safe from barge thread (no PortAudio calls)."""
    _stop_playback.set()
    _stream_tts_cancel.set()
    try:
        get_audio_gate().signal_playback_interrupt()
    except Exception:
        pass


def cancel_piper_synthesis() -> None:
    """Kill in-flight Piper subprocess (stream TTS). Safe from barge thread."""
    global _active_piper_proc
    _stream_tts_cancel.set()
    with _piper_lock:
        proc = _active_piper_proc
        _active_piper_proc = None
    if proc is None:
        return
    try:
        proc.kill()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def cancel_s2_synthesis() -> None:
    """Kill in-flight Fish s2.exe subprocess. Safe from barge thread."""
    try:
        from voice.fish_s2_backend import cancel_s2_synthesis as _cancel_s2

        _cancel_s2()
    except Exception:
        pass


def cancel_stream_tts() -> None:
    """Cancel chunked TTS: stop playback signal + kill Piper/S2 + mark cancel."""
    _stream_tts_cancel.set()
    request_playback_interrupt()
    cancel_piper_synthesis()
    cancel_s2_synthesis()


def stop_speaking(*, force_portaudio_stop: bool = True) -> None:
    """Interrupt current TTS playback (barge-in / stop).

    When called from the barge InputStream thread, pass force_portaudio_stop=False
    so sd.stop() runs only in the TTS owner thread after the input stream closes.
    Also cancels pending stream-TTS Piper work.
    """
    global _is_speaking
    cancel_stream_tts()
    if not force_portaudio_stop:
        with _speaking_lock:
            _is_speaking = False
        return
    try:
        with _speaking_lock:
            try:
                with get_audio_gate().with_portaudio("sd_stop"):
                    sd.stop()
            except Exception as exc:
                print(f"[BARGE] sd.stop ignored: {exc}")
            _is_speaking = False
    except Exception as exc:
        print(f"[BARGE] stop_speaking ignored: {exc}")
    try:
        from core.crash_log import note_runtime_event

        note_runtime_event("tts_stop", detail="stop_speaking")
    except Exception:
        pass


def _cache_get(key: str):
    if key not in _tts_cache:
        return None
    _tts_cache.move_to_end(key)
    return _tts_cache[key]


def _cache_put(key: str, audio, rate):
    vs = get_voice_settings()
    if vs.tts_cache_size <= 0:
        return
    _tts_cache[key] = (audio, rate)
    _tts_cache.move_to_end(key)
    while len(_tts_cache) > vs.tts_cache_size:
        _tts_cache.popitem(last=False)


def _generate_piper_wav(text: str, filename: str) -> bool:
    """Synthesize one Piper WAV. Uses Popen so stream cancel can kill the process."""
    global _active_piper_proc

    piper_exists = bool(PIPER) and (
        os.path.isfile(PIPER) or shutil.which(PIPER) is not None
    )
    if not piper_exists:
        print("❌ Nie znaleziono programu Piper.")
        return False
    if not os.path.isfile(VOICE_MODEL):
        print("❌ Nie znaleziono modelu głosu Piper.")
        return False

    proc = None
    try:
        proc = subprocess.Popen(
            [PIPER, "--model", VOICE_MODEL, "--output_file", filename],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        with _piper_lock:
            _active_piper_proc = proc
        try:
            proc.communicate(input=text, timeout=120)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            print("[TTS] Piper timeout")
            return False
        code = getattr(proc, "returncode", 0)
        # Only treat real integer non-zero codes as failure (mocks may be MagicMock).
        if isinstance(code, int) and code != 0:
            try:
                from core.crash_log import note_subprocess_exit

                note_subprocess_exit(
                    "piper",
                    code,
                    detail="tts_generate",
                )
            except Exception:
                pass
            print(f"[TTS] Piper exit_code={code}")
            return False
    except OSError as error:
        print("❌ Błąd uruchamiania Piper:", error)
        try:
            from core.crash_log import classify_and_recover

            classify_and_recover(
                error,
                subsystem="tts",
                classification="TTS_PROCESS_FAILURE",
            )
        except Exception:
            pass
        return False
    finally:
        with _piper_lock:
            if _active_piper_proc is proc:
                _active_piper_proc = None

    # If this synth was killed mid-stream, the WAV is incomplete / missing.
    if _stream_tts_cancel.is_set() and (
        proc is not None and getattr(proc, "returncode", None) not in (0, None)
    ):
        return False
    return os.path.exists(filename)


# ==========================
# MÓWIENIE
# ==========================

def speak(text, cooldown=None):
    global _is_speaking, _last_spoken_text

    print()
    print("🔊 Elian:", text)
    print()

    vs = get_voice_settings()
    if not vs.tts_enabled:
        return

    _last_spoken_text = str(text or "")

    tracker = current_tracker()
    if tracker:
        tracker.mark("tts_gen_start")

    _stop_playback.clear()
    _stream_tts_cancel.clear()
    try:
        get_audio_gate().clear_playback_interrupt()
    except Exception:
        pass
    cache_key = text.strip().lower()
    cached = _cache_get(cache_key)

    sounds_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Sounds"
    )
    filename = None
    gate = get_audio_gate()
    gate.begin_tts()

    try:
        # Do not start playback while a barge/listen InputStream is still closing.
        gate.wait_inputs_idle(timeout=1.5)

        if cached is not None:
            audio, rate = cached
        else:
            os.makedirs(sounds_dir, exist_ok=True)
            filename = os.path.join(sounds_dir, f"{uuid.uuid4()}.wav")
            if not _generate_piper_wav(text, filename):
                print("[TTS] generation failed")
                try:
                    from core.rc_trace import log_rc_trace

                    log_rc_trace(tts_generated="no", playback_started="no")
                except Exception:
                    pass
                return
            audio, rate = sf.read(filename)
            # Cache short phrases only
            if len(text) <= 40:
                _cache_put(cache_key, audio, rate)

        if tracker:
            tracker.mark("tts_gen_end")

        if _stop_playback.is_set():
            return

        with _speaking_lock:
            _is_speaking = True

        play_exc_last = None
        played = False
        src_audio, src_rate = audio, int(rate)
        # Prefer device default rate first — check_output_settings often lies on WASAPI.
        force_rates = (None, 48000, 44100, 22050)
        for device_idx in _output_device_candidates():
            label = f"#{device_idx}" if device_idx is not None else "default"
            for force in force_rates:
                try:
                    audio, rate = _fit_playback_rate(
                        src_audio, src_rate, device_idx, force_rate=force
                    )
                    with gate.with_portaudio(
                        "sd_play"
                        if device_idx is not None
                        else "sd_play_default"
                    ):
                        if device_idx is not None:
                            sd.play(audio, rate, device=device_idx)
                        else:
                            sd.play(audio, rate)
                    if device_idx != _OUTPUT_DEVICE:
                        print(f"[TTS] playback using output device #{device_idx}")
                    if force is not None:
                        print(f"[TTS] playback ok on {label} @ {rate} Hz")
                    played = True
                    break
                except Exception as play_exc:
                    play_exc_last = play_exc
                    print(f"[TTS] playback failed on {label}: {play_exc}")
                    if force is None and not _is_invalid_sample_rate(play_exc):
                        # Non-rate errors: skip forced rates on this device.
                        break
                    continue
            if played:
                break
        if not played:
            print(f"[TTS] playback failed: {play_exc_last}")
            try:
                from core.crash_log import classify_and_recover

                classify_and_recover(
                    play_exc_last or RuntimeError("TTS playback failed"),
                    subsystem="tts",
                    classification="TTS_PLAYBACK_FAILURE",
                )
            except Exception:
                pass
            return

        if tracker:
            tracker.mark("tts_first_audio")
            tracker.log()

        # Interruptible wait: sd.wait in a side thread so stop_speaking() works
        done = threading.Event()

        def _wait_playback():
            try:
                sd.wait()
            finally:
                done.set()

        waiter = threading.Thread(
            target=_wait_playback, name="elian-tts-wait", daemon=True
        )
        waiter.start()
        while not done.wait(timeout=0.05):
            if _stop_playback.is_set():
                # Owner thread performs the single PortAudio stop.
                try:
                    with gate.with_portaudio("sd_stop_owner"):
                        sd.stop()
                except Exception:
                    pass
                break
        waiter.join(timeout=2.0)

        pause = TTS_COOLDOWN if cooldown is None else float(cooldown)
        if pause > 0 and not _stop_playback.is_set():
            print(f"⏳ Pauza po TTS: {pause:.2f}s")
            time.sleep(pause)

    finally:
        with _speaking_lock:
            _is_speaking = False
        gate.end_tts()
        if filename and os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError:
                pass


def speak_from_wav(text, wav_path, cooldown=None):
    """Play a pre-generated WAV through the existing TTS playback path (no Piper)."""
    global _is_speaking, _last_spoken_text

    print()
    print("🔊 Elian:", text)
    print()

    vs = get_voice_settings()
    if not vs.tts_enabled:
        return False
    if not wav_path or not os.path.isfile(wav_path):
        print("[TTS] speak_from_wav: missing wav")
        return False

    _last_spoken_text = str(text or "")
    tracker = current_tracker()
    # Playback-only path: do NOT zero tts_gen_* (synthesis already marked upstream).
    if tracker and "tts_gen_start" not in tracker.marks:
        # Piper/direct callers that skipped Fish wrapper — mark playback queue only.
        tracker.mark("tts_playback_start")
    elif tracker:
        tracker.mark("tts_playback_start")

    _stop_playback.clear()
    _stream_tts_cancel.clear()
    try:
        get_audio_gate().clear_playback_interrupt()
    except Exception:
        pass

    gate = get_audio_gate()
    gate.begin_tts()
    try:
        gate.wait_inputs_idle(timeout=1.5)
        audio, rate = sf.read(wav_path)
        if _stop_playback.is_set():
            return False
        with _speaking_lock:
            _is_speaking = True

        play_exc_last = None
        played = False
        src_audio, src_rate = audio, int(rate)
        force_rates = (None, 48000, 44100, 22050)
        for device_idx in _output_device_candidates():
            for force in force_rates:
                try:
                    audio, rate = _fit_playback_rate(
                        src_audio, src_rate, device_idx, force_rate=force
                    )
                    with gate.with_portaudio(
                        "sd_play" if device_idx is not None else "sd_play_default"
                    ):
                        if device_idx is not None:
                            sd.play(audio, rate, device=device_idx)
                        else:
                            sd.play(audio, rate)
                    played = True
                    break
                except Exception as play_exc:
                    play_exc_last = play_exc
                    if force is None and not _is_invalid_sample_rate(play_exc):
                        break
                    continue
            if played:
                break
        if not played:
            print(f"[TTS] speak_from_wav playback failed: {play_exc_last}")
            return False

        if tracker:
            tracker.mark("tts_first_audio")
            tracker.log()

        done = threading.Event()

        def _wait_playback():
            try:
                sd.wait()
            finally:
                done.set()
                if tracker:
                    tracker.mark("tts_playback_end")

        waiter = threading.Thread(
            target=_wait_playback, name="elian-tts-wait", daemon=True
        )
        waiter.start()
        while not done.wait(timeout=0.05):
            if _stop_playback.is_set():
                try:
                    with gate.with_portaudio("sd_stop_owner"):
                        sd.stop()
                except Exception:
                    pass
                break
        waiter.join(timeout=2.0)

        pause = TTS_COOLDOWN if cooldown is None else float(cooldown)
        if pause > 0 and not _stop_playback.is_set():
            time.sleep(pause)
        try:
            from core.crash_log import clear_degraded_subsystem

            clear_degraded_subsystem("tts")
        except Exception:
            pass
        return True
    finally:
        with _speaking_lock:
            _is_speaking = False
        gate.end_tts()


class StreamSpeakResult:
    """Outcome of speak_stream / chunked TTS."""

    __slots__ = ("spoken_text", "interrupted", "generated_text", "chunks_spoken")

    def __init__(
        self,
        spoken_text: str = "",
        *,
        interrupted: bool = False,
        generated_text: str = "",
        chunks_spoken: int = 0,
    ):
        self.spoken_text = spoken_text
        self.interrupted = interrupted
        self.generated_text = generated_text
        self.chunks_spoken = chunks_spoken


def _play_pcm_interruptible(audio, rate) -> bool:
    """Play one PCM buffer; return False if interrupted or playback failed."""
    gate = get_audio_gate()
    src_audio, src_rate = audio, int(rate)
    force_rates = (None, 48000, 44100, 22050)
    played = False
    for device_idx in _output_device_candidates():
        for force in force_rates:
            try:
                fitted, out_rate = _fit_playback_rate(
                    src_audio, src_rate, device_idx, force_rate=force
                )
                with gate.with_portaudio(
                    "sd_play" if device_idx is not None else "sd_play_default"
                ):
                    if device_idx is not None:
                        sd.play(fitted, out_rate, device=device_idx)
                    else:
                        sd.play(fitted, out_rate)
                played = True
                break
            except Exception:
                if force is None:
                    break
                continue
        if played:
            break
    if not played:
        return False

    done = threading.Event()

    def _wait_playback():
        try:
            sd.wait()
        finally:
            done.set()

    waiter = threading.Thread(
        target=_wait_playback, name="elian-tts-wait", daemon=True
    )
    waiter.start()
    while not done.wait(timeout=0.05):
        if _stop_playback.is_set() or _stream_tts_cancel.is_set():
            try:
                with gate.with_portaudio("sd_stop_owner"):
                    sd.stop()
            except Exception:
                pass
            waiter.join(timeout=2.0)
            return False
    waiter.join(timeout=2.0)
    return not (_stop_playback.is_set() or _stream_tts_cancel.is_set())


def speak_stream(
    token_source,
    *,
    clause_flush_chars=None,
    cooldown=None,
    synthesize_fn=None,
    play_fn=None,
    cancel_check=None,
):
    """Stream tokens → speech chunks → Piper (one ahead) → playback.

    ``token_source``: iterable of str deltas (Ollama stream) or a single str.
    Injectable ``synthesize_fn(text) -> (audio, rate)`` and
    ``play_fn(audio, rate) -> bool`` support offline tests.
    """
    global _is_speaking, _last_spoken_text, _spoken_stream_parts

    vs = get_voice_settings()
    if not vs.tts_enabled:
        # Still consume tokens so callers can commit full text if desired.
        generated = "".join(str(t or "") for t in token_source)
        return StreamSpeakResult(spoken_text="", generated_text=generated)

    if clause_flush_chars is None:
        clause_flush_chars = int(getattr(vs, "tts_clause_flush_chars", 50) or 0)

    from voice.speech_chunker import iter_speech_chunks

    _stream_tts_cancel.clear()
    _stop_playback.clear()
    try:
        get_audio_gate().clear_playback_interrupt()
    except Exception:
        pass

    _spoken_stream_parts = []
    generated_parts: list[str] = []
    spoken_parts: list[str] = []
    interrupted = False

    def _cancelled() -> bool:
        if _stream_tts_cancel.is_set() or _stop_playback.is_set():
            return True
        if cancel_check is not None:
            try:
                if cancel_check():
                    return True
            except Exception:
                pass
        return False

    def _default_synthesize(text: str):
        sounds_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "Sounds",
        )
        os.makedirs(sounds_dir, exist_ok=True)
        filename = os.path.join(sounds_dir, f"{uuid.uuid4()}.wav")
        try:
            if not _generate_piper_wav(text, filename):
                return None
            audio, rate = sf.read(filename)
            return audio, int(rate)
        finally:
            if os.path.exists(filename):
                try:
                    os.remove(filename)
                except OSError:
                    pass

    synth = synthesize_fn or _default_synthesize
    play = play_fn or _play_pcm_interruptible

    # token_source may be a plain string
    if isinstance(token_source, str):
        token_iter = iter([token_source])
    else:
        token_iter = iter(token_source)

    def _token_tee():
        for piece in token_iter:
            if _cancelled():
                return
            text = str(piece or "")
            if text:
                generated_parts.append(text)
                yield text

    text_q: Queue = Queue(maxsize=2)
    audio_q: Queue = Queue(maxsize=2)
    sentinel = object()

    def _feeder():
        try:
            for chunk in iter_speech_chunks(
                _token_tee(),
                clause_flush_chars=clause_flush_chars,
            ):
                if _cancelled():
                    break
                chunk = str(chunk or "").strip()
                if not chunk:
                    continue
                try:
                    print()
                    print("Elian:", chunk)
                    print()
                except Exception:
                    pass
                while not _cancelled():
                    try:
                        text_q.put(chunk, timeout=0.05)
                        break
                    except Exception:
                        continue
        finally:
            try:
                text_q.put(sentinel, timeout=1.0)
            except Exception:
                pass

    def _synth_worker():
        try:
            while not _cancelled():
                try:
                    item = text_q.get(timeout=0.05)
                except Empty:
                    continue
                if item is sentinel:
                    break
                if _cancelled():
                    break
                result = None
                try:
                    result = synth(str(item))
                except Exception as exc:
                    print(f"[TTS] stream synth failed: {exc}")
                if _cancelled() or not result:
                    continue
                audio, rate = result
                while not _cancelled():
                    try:
                        audio_q.put((audio, rate, str(item)), timeout=0.05)
                        break
                    except Exception:
                        continue
        finally:
            try:
                audio_q.put(sentinel, timeout=1.0)
            except Exception:
                pass

    gate = get_audio_gate()
    gate.begin_tts()
    feeder = threading.Thread(target=_feeder, name="elian-tts-feed", daemon=True)
    worker = threading.Thread(target=_synth_worker, name="elian-tts-synth", daemon=True)
    tracker = current_tracker()
    first_audio = False

    try:
        gate.wait_inputs_idle(timeout=1.5)
        with _speaking_lock:
            _is_speaking = True
        feeder.start()
        worker.start()

        while True:
            if _cancelled():
                interrupted = True
                break
            try:
                item = audio_q.get(timeout=0.05)
            except Empty:
                if not feeder.is_alive() and not worker.is_alive() and audio_q.empty():
                    break
                continue
            if item is sentinel:
                break
            if _cancelled():
                interrupted = True
                break
            audio, rate, chunk_text = item
            if tracker and not first_audio:
                tracker.mark("tts_first_audio")
                first_audio = True
            ok = play(audio, rate)
            # Count chunk as spoken if playback started/finished successfully,
            # even when barge-in arrives at the end of the chunk.
            if ok:
                spoken_parts.append(chunk_text)
                _spoken_stream_parts = list(spoken_parts)
                _last_spoken_text = " ".join(spoken_parts)
            if not ok or _cancelled():
                interrupted = True
                break

        if interrupted:
            cancel_piper_synthesis()

        pause = TTS_COOLDOWN if cooldown is None else float(cooldown)
        if pause > 0 and not interrupted and not _cancelled():
            print(f"⏳ Pauza po TTS: {pause:.2f}s")
            time.sleep(pause)

        if tracker and first_audio:
            try:
                tracker.log()
            except Exception:
                pass
    finally:
        _stream_tts_cancel.set()
        cancel_piper_synthesis()
        feeder.join(timeout=2.0)
        worker.join(timeout=2.0)
        with _speaking_lock:
            _is_speaking = False
        gate.end_tts()

    spoken = " ".join(s.strip() for s in spoken_parts if s.strip())
    _last_spoken_text = spoken
    return StreamSpeakResult(
        spoken_text=spoken,
        interrupted=interrupted,
        generated_text="".join(generated_parts),
        chunks_spoken=len(spoken_parts),
    )


# ==========================
# POZIOM DŹWIĘKU
# ==========================

def get_volume(audio):
    if len(audio) == 0:
        return 0
    return float(np.sqrt(np.mean(audio ** 2)))


# ==========================
# NAGRYWANIE
# ==========================

def record_until_silence(wait_timeout=None):
    print("🎤 Czekam na mowę...")

    vs = get_voice_settings()
    silence_limit = vs.vad_silence_ms / 1000.0
    min_speech = vs.vad_min_speech_ms / 1000.0
    capture_rate = int(_CAPTURE_RATE or RATE)
    stt_rate = int(RATE)
    capture_block = max(64, int(round(BLOCK * float(capture_rate) / float(stt_rate))))
    pre_roll_blocks = max(1, int((vs.pre_roll_ms / 1000.0) * stt_rate / BLOCK))

    audio_queue = Queue()
    chunks = []
    pre_roll = deque(maxlen=pre_roll_blocks)
    speaking = False
    silence_start = None
    wait_start = time.time()
    speech_start = None
    peak_wait_rms = 0.0
    last_diag_at = 0.0
    got_callback = False

    current_vad = get_vad_model()
    if hasattr(current_vad, "reset_states"):
        current_vad.reset_states()

    def callback(indata, frames, callback_time, status):
        nonlocal got_callback
        got_callback = True
        if status:
            print("Audio:", status)
        audio_queue.put(indata.copy())

    gate = get_audio_gate()
    # Barge InputStream must fully close before listen opens another.
    gate.wait_inputs_idle(timeout=2.0)
    try:
        from voice.mic_mute import ensure_capture_unmuted

        auto = bool(get_config_value("voice", "auto_unmute_mic", default=True))
        ensure_capture_unmuted(auto_unmute=auto)
    except Exception:
        pass
    try:
        from core.crash_log import write_runtime_snapshot

        write_runtime_snapshot(reason="before_listen_input", gpu_op="", persist=False)
    except Exception:
        pass

    def _to_stt_block(raw) -> np.ndarray:
        samples = np.asarray(raw, dtype=np.float32).flatten()
        if capture_rate != stt_rate:
            samples = _resample_audio(samples, capture_rate, stt_rate)
        return samples.reshape(-1, 1)

    def _build_stream_kwargs(device_idx):
        kwargs = {
            "samplerate": capture_rate,
            "channels": CHANNELS,
            "dtype": "float32",
            "blocksize": capture_block,
            "callback": callback,
        }
        if device_idx is not None:
            kwargs["device"] = device_idx
            # Shared WASAPI so Windows mic levels apply (exclusive → silence).
            try:
                if hasattr(sd, "WasapiSettings"):
                    host = sd.query_hostapis(
                        sd.query_devices(device_idx)["hostapi"]
                    )["name"]
                    if "WASAPI" in str(host).upper():
                        kwargs["extra_settings"] = sd.WasapiSettings(
                            exclusive=False
                        )
            except Exception:
                pass
        return kwargs

    def _open_listen_stream():
        """Open mic with name re-resolve; never crash on -9996/-9985 storm."""
        nonlocal capture_rate, capture_block
        tried: set[int] = set()
        device = _INPUT_DEVICE
        last_exc: BaseException | None = None
        tried_default = False

        for _attempt in range(5):
            kwargs = _build_stream_kwargs(device)
            try:
                return sd.InputStream(**kwargs)
            except Exception as exc:
                last_exc = exc
                print(f"[AUDIO] Input open failed ({exc})")
                if device is not None:
                    tried.add(int(device))
                # Prefer re-bind by configured name over blind Windows default.
                if _is_portaudio_device_error(exc) or device is not None:
                    new_idx = reresolve_input_device(exclude=tried)
                    if new_idx is not None and int(new_idx) not in tried:
                        device = int(new_idx)
                        capture_rate = int(_CAPTURE_RATE or RATE)
                        capture_block = max(
                            64,
                            int(
                                round(
                                    BLOCK * float(capture_rate) / float(stt_rate)
                                )
                            ),
                        )
                        continue
                if not tried_default:
                    tried_default = True
                    print(
                        "[AUDIO] Named mic failed — trying Windows default once"
                    )
                    device = None
                    continue
                break

        print(f"[AUDIO] Cannot open microphone after recovery: {last_exc}")
        return None

    with gate.input_stream_slot(wait_for_tts=True):
        stream_ctx = _open_listen_stream()
        if stream_ctx is None:
            return None

        with stream_ctx:
            while True:
                now = time.time()

                if (
                    not speaking
                    and wait_timeout is not None
                    and now - wait_start >= wait_timeout
                ):
                    break

                if speaking and now - speech_start >= MAX_RECORDING:
                    print("⏱️ Maksymalny czas nagrania.")
                    break

                try:
                    raw = audio_queue.get(timeout=capture_block / float(capture_rate))
                except Empty:
                    if (
                        not speaking
                        and now - wait_start >= 2.5
                        and now - last_diag_at >= 2.5
                    ):
                        last_diag_at = now
                        if not got_callback:
                            print(
                                "[AUDIO] brak próbek z mikrofonu — sprawdź mute / inne appki"
                            )
                        else:
                            print(
                                f"[AUDIO] czekam… peak_rms={peak_wait_rms:.4f} "
                                f"(thr={ENERGY_THRESHOLD})"
                            )
                            peak_wait_rms = 0.0
                    continue

                audio = _to_stt_block(raw)
                volume = get_volume(audio)
                if not speaking:
                    if volume > peak_wait_rms:
                        peak_wait_rms = volume
                    if now - last_diag_at >= 2.5:
                        last_diag_at = now
                        print(
                            f"[AUDIO] czekam… peak_rms={peak_wait_rms:.4f} "
                            f"(thr={ENERGY_THRESHOLD})"
                        )
                        peak_wait_rms = 0.0

                if _speech_gate(audio, volume):
                    if not speaking:
                        speaking = True
                        speech_start = time.time()
                        chunks.extend(pre_roll)
                        pre_roll.clear()
                        print("🎤 WYKRYTO MOWĘ!")
                        try:
                            from core.latency import current_tracker

                            tracker = current_tracker()
                            if tracker is not None and "capture_start" not in tracker.marks:
                                tracker.mark("capture_start")
                        except Exception:
                            pass
                    chunks.append(audio)
                    silence_start = None

                elif speaking:
                    chunks.append(audio)
                    speech_len = time.time() - (speech_start or time.time())
                    if silence_start is None:
                        silence_start = time.time()
                    elif (
                        time.time() - silence_start >= silence_limit
                        and speech_len >= min_speech
                    ):
                        print("🔇 Koniec wypowiedzi.")
                        break
                else:
                    pre_roll.append(audio)

    if not speaking or not chunks:
        return None

    audio = np.concatenate(chunks, axis=0)
    return audio.flatten()


# ==========================
# ROZPOZNAWANIE
# ==========================

def _should_llm_correct(text: str, llm_correct: bool) -> bool:
    """Skip TextCorrector LLM for short / obvious command-like phrases."""
    if not llm_correct:
        return False
    if not bool(get_config_value("ai", "llm_correction", default=True)):
        return False
    words = text.split()
    if len(words) <= 4 and len(text) <= 40:
        # Fast-path style utterances — dictionary corrections only
        return False
    return True


def _clip_too_quiet(audio) -> bool:
    """Reject near-silence / room bleed before Whisper (avoids empty+hallucinations)."""
    try:
        rms = float(np.sqrt(np.mean(np.asarray(audio, dtype=np.float32) ** 2)))
    except Exception:
        return False
    # Real close-talk on Plantronics is typically >> energy_threshold after gain.
    # Keep floor modest so short commands are not discarded; reject pure noise.
    floor = max(float(ENERGY_THRESHOLD) * 0.5, 0.0025)
    if rms < floor:
        print(f"[AUDIO] pomijam cichy klip rms={rms:.4f} (floor={floor:.4f})")
        return True
    return False


def _normalize_stt_audio(audio: np.ndarray) -> np.ndarray:
    """Boost quiet close-talk into a Whisper-friendly RMS without clipping hard."""
    samples = np.asarray(audio, dtype=np.float32).flatten()
    if samples.size == 0:
        return samples
    rms = float(np.sqrt(np.mean(samples ** 2)))
    if rms < 1e-6:
        return samples
    target = 0.08
    # Only boost; never attenuate loud speech.
    if rms >= target * 0.85:
        print(f"[AUDIO] clip rms={rms:.4f} (no gain)")
        return samples
    gain = min(24.0, target / rms)
    print(f"[AUDIO] clip rms={rms:.4f} -> STT gain x{gain:.1f}")
    return np.clip(samples * gain, -1.0, 1.0).astype(np.float32)


def transcribe_audio(audio, llm_correct=False) -> str:
    """Run Whisper on float32 PCM @ RATE. Model stays warm (singleton)."""
    if audio is None or len(audio) == 0:
        return ""

    if _clip_too_quiet(audio):
        return ""

    audio = _normalize_stt_audio(audio)

    tracker = current_tracker()
    if tracker:
        tracker.mark("stt_start")

    print("🧠 Rozpoznawanie mowy...")

    try:
        segments, info = get_whisper_model().transcribe(
            audio,
            language=WHISPER_LANGUAGE,
            beam_size=5,
            best_of=5,
            temperature=0,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,
            no_speech_threshold=0.5,
            log_prob_threshold=-1.0,
        )

        text = ""
        for segment in segments:
            text += segment.text
        text = text.strip()

        if tracker:
            tracker.mark("stt_end")

        if not text:
            print("❌ Nic nie rozpoznano.")
            return ""

        print()
        print("📝 WHISPER:")
        print(text)

        corrected = apply_known_corrections(text)
        if _should_llm_correct(corrected, llm_correct):
            corrected = correct_text(corrected)

        if corrected != text:
            print()
            print("🧹 PO KOREKCIE:")
            print(corrected)
            print()
        else:
            print()

        from core.transcript import envelope_from_whisper, set_last_transcript

        envelope = envelope_from_whisper(text, corrected=corrected)
        set_last_transcript(envelope)
        # Command path uses corrected STT (known phonetic fixes). Raw stays on
        # envelope for diagnostics / casing audits — never force .lower() here.
        command = str(corrected or text or "").strip()
        return command or envelope.raw_transcript

    except Exception as e:
        print("❌ Błąd Whisper:", e)
        try:
            from core.crash_log import classify_and_recover, note_runtime_event

            note_runtime_event("stt_failure", detail=str(e))
            classify_and_recover(
                e,
                subsystem="stt",
                classification="STT_WORKER_FAILURE",
            )
        except Exception:
            pass
        return ""


def listen(wait_timeout=None, llm_correct=False):
    audio = record_until_silence(wait_timeout=wait_timeout)

    tracker = current_tracker()
    if tracker and "capture_end" not in tracker.marks:
        tracker.mark("capture_end")

    if audio is None or len(audio) == 0:
        return ""

    return transcribe_audio(audio, llm_correct=llm_correct)


def shutdown_voice():
    """Release playback / clear speaking flag (wake closed by pipeline)."""
    stop_speaking()
    global _is_speaking
    with _speaking_lock:
        _is_speaking = False
