import os
import tempfile
import time

import numpy as np
import scipy.io.wavfile as wav
import sounddevice as sd
import torch
from faster_whisper import WhisperModel
from silero_vad import get_speech_timestamps, load_silero_vad

from Config import get_config_value


SAMPLE_RATE = 16000
MICROPHONE = 1

CHUNK_SECONDS = 0.5
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECONDS)

ENERGY_THRESHOLD = 0.005
SILENCE_SECONDS = 1.0
MAX_RECORDING_SECONDS = 15


vad_model = None
whisper = None


def load_models():

    global vad_model, whisper

    if vad_model is None:

        print(
            "Ładowanie Silero VAD...",
            flush=True
        )

        vad_model = load_silero_vad()

        print("VAD OK", flush=True)

    if whisper is None:

        print(
            "Ładowanie Faster-Whisper...",
            flush=True
        )

        whisper = WhisperModel(
            get_config_value(
                "whisper",
                "model",
                default="small",
                legacy_key="whisper_model"
            ),
            device=get_config_value(
                "whisper",
                "device",
                default="auto"
            ),
            compute_type=get_config_value(
                "whisper",
                "compute_type",
                default="default"
            )
        )

        print("Whisper OK", flush=True)

    return vad_model, whisper


def detect_speech(audio):

    current_vad, _ = load_models()

    tensor = torch.from_numpy(
        audio.astype(np.float32)
    )

    timestamps = get_speech_timestamps(
        tensor,
        current_vad,
        sampling_rate=SAMPLE_RATE
    )

    return len(timestamps) > 0


def record_sentence():

    chunks = []
    speaking = False
    silence_start = None
    start_time = None

    print("🎤 Czekam na mowę...", flush=True)

    with sd.InputStream(
        device=MICROPHONE,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK_SIZE
    ) as stream:

        while True:

            audio, overflowed = stream.read(
                CHUNK_SIZE
            )

            audio = audio[:, 0]

            volume = np.sqrt(
                np.mean(audio ** 2)
            )

            speech = (
                volume >= ENERGY_THRESHOLD
                and detect_speech(audio)
            )

            if speech:

                if not speaking:

                    print(
                        "\n🎤 WYKRYTO MOWĘ!",
                        flush=True
                    )

                    speaking = True
                    start_time = time.time()

                chunks.append(audio.copy())
                silence_start = None

            elif speaking:

                chunks.append(audio.copy())

                if silence_start is None:

                    silence_start = time.time()

                if (
                    time.time() - silence_start
                    >= SILENCE_SECONDS
                ):

                    print(
                        "🔇 Koniec wypowiedzi.",
                        flush=True
                    )

                    break

            if (
                speaking
                and time.time() - start_time
                >= MAX_RECORDING_SECONDS
            ):

                print(
                    "⏱️ Maksymalna długość nagrania.",
                    flush=True
                )

                break

    if not chunks:

        return None

    return np.concatenate(chunks)


def transcribe(audio):

    print(
        "🧠 Rozpoznawanie mowy...",
        flush=True
    )

    _, current_whisper = load_models()

    audio_int16 = (
        audio * 32767
    ).astype(np.int16)

    filename = None

    try:

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False
        ) as file:

            filename = file.name

        wav.write(
            filename,
            SAMPLE_RATE,
            audio_int16
        )

        segments, info = current_whisper.transcribe(
            filename,
            language=get_config_value(
                "whisper",
                "language",
                default="pl"
            ),
            beam_size=5,
            vad_filter=False
        )

        return "".join(
            segment.text
            for segment in segments
        ).strip()

    finally:

        if filename and os.path.exists(filename):

            os.remove(filename)


def main():

    load_models()

    print()
    print("================================")
    print("ELIAN GOTOWY")
    print("================================")
    print("Mikrofon:", MICROPHONE)
    print("Powiedz coś...")
    print()

    while True:

        try:

            audio = record_sentence()

            if audio is None:

                continue

            text = transcribe(audio)

            if text:

                print()
                print("================================")
                print("TY:", text)
                print("================================")
                print()

        except KeyboardInterrupt:

            print()
            print("Zamykanie Eliana...")

            break

        except Exception as error:

            print()
            print("❌ BŁĄD:")
            print(error)
            print()

            time.sleep(1)


if __name__ == "__main__":

    main()
