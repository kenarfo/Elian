# ==========================
# ELIAN AI ASSISTANT
# TextCorrector.py
# Version 0.7
# ==========================

import ollama
import re
import unicodedata
from difflib import SequenceMatcher

from Config import get_config_value


MODEL = get_config_value(
    "ai",
    "ollama_model",
    default="llama3.1:8b",
    legacy_key="ollama_model"
)

CLIENT = ollama.Client(
    timeout=float(
        get_config_value(
            "ai",
            "request_timeout",
            default=30
        )
    )
)


SYSTEM_PROMPT = """
Jesteś korektorem tekstu pochodzącego z rozpoznawania mowy.

Twoim zadaniem jest POPRAWIĆ BŁĘDY ROZPOZNAWANIA MOWY.

BARDZO WAŻNE ZASADY:

1. Nie zmieniaj znaczenia wypowiedzi.
2. Nie dopisuj żadnych informacji.
3. Nie odpowiadaj użytkownikowi.
4. Nie komentuj tekstu.
5. Zwróć WYŁĄCZNIE poprawiony tekst.
6. Jeżeli tekst jest poprawny, zwróć go bez zmian.
7. Popraw oczywiste błędy fonetyczne i literówki.
8. Zachowaj styl wypowiedzi użytkownika.
9. Nie zamieniaj krótkiej wypowiedzi w dłuższą.
10. Jeżeli nie jesteś pewien poprawki, pozostaw oryginalne słowo.

Znane nazwy:

Elian
Chrome
Google
YouTube
Spotify
Discord
Minecraft
Microsoft Store
Ollama
Whisper
Silero
Smart Home
Home Assistant
ESP32
Raspberry Pi

Przykłady:

"hej eljan otwórz krom"
-> "hej elian otwórz chrome"

"hej elian jaka dzis pogoda"
-> "hej elian jaka dziś pogoda"

"otworz kalkulator"
-> "otwórz kalkulator"

Nie wolno Ci odpowiadać:
"Jasne!"
"Poprawiony tekst:"
"Chodziło Ci o..."
itp.

Masz zwrócić tylko tekst.
"""


def clean_response(text):

    text = text.strip()

    # Usuwamy przypadkowe cudzysłowy
    text = text.strip('"')
    text = text.strip("'")

    # Usuwamy typowe prefiksy, gdy model
    # mimo wszystko je doda
    prefixes = [
        "poprawiony tekst:",
        "poprawiony tekst",
        "tekst:",
        "wynik:"
    ]

    lower = text.lower()

    for prefix in prefixes:

        if lower.startswith(prefix):

            text = text[len(prefix):].strip()

            break

    return text


KNOWN_CORRECTIONS = {
    "eljan": "elian",
    "ilian": "elian",
    "krom": "chrome",
    "chrom": "chrome",
    "jutub": "youtube",
    "jutube": "youtube",
    "spotifaj": "spotify",
    "klime": "klimę",
    "klimy": "klimy",
    # Whisper often fuses short Polish light cues (L2 simulated voice evidence).
    "czemnotu": "ciemno tu",
    "ciemnotu": "ciemno tu",
}


def apply_known_corrections(text):

    if not text:

        return ""

    tokens = re.findall(r"[A-Za-zÀ-ž0-9]+|[^A-Za-zÀ-ž0-9]+", text)
    result = []

    for token in tokens:

        key = _normalize(token)
        replacement = KNOWN_CORRECTIONS.get(key)

        if replacement and token.isalpha():

            if token[0].isupper():

                replacement = replacement[:1].upper() + replacement[1:]

            result.append(replacement)

        else:

            result.append(token)

    return "".join(result)


def should_llm_correct(text):

    if not bool(
        get_config_value(
            "ai",
            "llm_correction",
            default=True
        )
    ):

        return False

    stripped = text.strip()

    if len(stripped) < 12:

        return False

    lowered = stripped.lower()
    skip_markers = (
        "która godzina",
        "ktora godzina",
        "jaka jest data",
        "jaki dziś",
        "jaki dzis",
        "otwórz chrome",
        "otworz chrome",
        "włącz chrome",
        "wlacz chrome",
    )

    if any(marker in lowered for marker in skip_markers):

        return False

    return True

OPPOSITE_GROUPS = (
    ({"wlacz", "zapal"}, {"wylacz", "zgas"}),
    ({"otworz"}, {"zamknij"}),
    ({"glosniej", "zwieksz"}, {"ciszej", "zmniejsz"}),
    ({"cieple", "ciepla"}, {"zimne", "zimna"}),
    ({"tak"}, {"nie"}),
)


def _normalize(value):
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _tokens(value):
    return _normalize(value).split()


def _changes_are_phonetic(original, corrected):
    original_tokens = _tokens(original)
    corrected_tokens = _tokens(corrected)
    matcher = SequenceMatcher(None, original_tokens, corrected_tokens)

    changed = 0
    limit = max(2, int(len(original_tokens) * 0.3 + 0.5))

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue

        before = original_tokens[i1:i2]
        after = corrected_tokens[j1:j2]
        changed += max(len(before), len(after))
        if changed > limit:
            return False

        if (
            len(before) == 1
            and len(after) == 1
            and KNOWN_CORRECTIONS.get(before[0]) == after[0]
        ):
            continue

        before_joined = "".join(before)
        after_joined = "".join(after)
        if not before_joined or not after_joined:
            return False

        similarity = SequenceMatcher(
            None,
            before_joined,
            after_joined,
        ).ratio()
        if similarity < 0.72:
            return False

    return True


def is_safe_correction(original, corrected):
    if not corrected or "\n" in corrected or "\r" in corrected:
        return False

    original_normalized = _normalize(original)
    corrected_normalized = _normalize(corrected)
    if not original_normalized or not corrected_normalized:
        return False

    original_length = len(original_normalized)
    corrected_length = len(corrected_normalized)
    if original_length >= 12 and not (
        original_length * 0.75
        <= corrected_length
        <= original_length * 1.3
    ):
        return False

    if re.findall(r"\d+(?:[.,]\d+)?", original) != re.findall(
        r"\d+(?:[.,]\d+)?",
        corrected,
    ):
        return False

    original_words = set(_tokens(original))
    corrected_words = set(_tokens(corrected))
    for first, second in OPPOSITE_GROUPS:
        if (
            original_words & first
            and corrected_words & second
        ) or (
            original_words & second
            and corrected_words & first
        ):
            return False

    similarity = SequenceMatcher(
        None,
        original_normalized,
        corrected_normalized,
    ).ratio()
    if similarity < 0.72:
        return False

    return _changes_are_phonetic(original, corrected)


def correct_text(text):

    if not text:
        return ""


    # Bardzo krótkich tekstów nie ma sensu
    # wysyłać do Ollamy.

    if len(text.strip()) < 4:
        return text

    if not should_llm_correct(text):
        return apply_known_corrections(text)


    try:

        response = CLIENT.chat(

            model=MODEL,

            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": text
                }
            ],

            options={
                "temperature": 0
            }
        )


        result = response[
            "message"
        ][
            "content"
        ].strip()


        result = clean_response(
            result
        )


        if not result:

            return text


        if not is_safe_correction(text, result):

            print(
                "Odrzucono korektę zmieniającą treść."
            )

            return text


        return result


    except Exception as e:

        print(
            "Korektor Ollama:",
            e
        )

        # Jeżeli korektor nie działa,
        # NIE zatrzymujemy Eliana.
        return text
