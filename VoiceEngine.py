import os
import tempfile

import scipy.io.wavfile as wav
import sounddevice as sd
from faster_whisper import WhisperModel

from Config import get_config_value


model = None


def get_model():

    global model

    if model is not None:

        return model

    print("Ładowanie Whisper...")

    model = WhisperModel(
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

    print("Whisper gotowy!")

    return model


def record_audio(seconds=5):

    print("🎤 Słucham...")

    sample_rate = 16000

    audio = sd.rec(
        int(seconds * sample_rate),
        samplerate=sample_rate,
        channels=1,
        dtype="int16"
    )

    sd.wait()

    return audio, sample_rate


def transcribe(audio, rate):

    filename = None

    try:

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False
        ) as file:

            filename = file.name

        wav.write(
            filename,
            rate,
            audio
        )

        segments, info = get_model().transcribe(
            filename,
            language=get_config_value(
                "whisper",
                "language",
                default="pl"
            )
        )

        return "".join(
            segment.text
            for segment in segments
        ).strip()

    finally:

        if filename and os.path.exists(filename):

            os.remove(filename)


def main():

    get_model()

    while True:

        try:

            input("ENTER aby mówić...")

            audio, rate = record_audio()

            text = transcribe(
                audio,
                rate
            )

            print("Ty:", text)

        except KeyboardInterrupt:

            print()
            print("Zamykanie testu głosu...")

            break


if __name__ == "__main__":

    main()
