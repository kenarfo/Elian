# ==========================
# ELIAN AI ASSISTANT
# Version 0.7
# Skills/Music.py
# ==========================

import re
import webbrowser
import pyautogui

from AppLauncher import launch_application


def match(text):
    text = text.lower().strip()
    patterns = (
        r"^(?:włącz|wlacz|puść|pusc)\s+muzykę$",
        r"^(?:włącz|wlacz)\s+youtube$",
        r"^(?:otwórz|otworz|włącz|wlacz)\s+spotify$",
        r"^(?:ciszej|głośniej|glosniej)$",
        r"^(?:zmniejsz|zwiększ|zwieksz)\s+głośność$",
        r"^wycisz(?:\s+(?:muzykę|dźwięk|dzwiek|komputer))?$",
        r"^(?:pauza|zatrzymaj muzykę)$",
    )
    return any(re.match(pattern, text) for pattern in patterns)


def run(text):
    text = text.lower().strip()

    if not match(text) and text not in ("ciszej", "głośniej", "glosniej", "wycisz", "pauza"):
        return None

    if (
        "włącz muzykę" in text
        or "puść muzykę" in text
        or re.match(r"^(?:włącz|wlacz)\s+youtube$", text)
    ):
        webbrowser.open("https://youtube.com")
        return "Otwieram YouTube."

    if "spotify" in text:
        _success, message = launch_application("Spotify")
        return message

    if "ciszej" in text or "zmniejsz głośność" in text:
        pyautogui.press("volumedown")
        return "Zmniejszam głośność."

    if "głośniej" in text or "glosniej" in text or "zwiększ głośność" in text:
        pyautogui.press("volumeup")
        return "Zwiększam głośność."

    if text.startswith("wycisz"):
        pyautogui.press("volumemute")
        return "Wyciszam."

    if "pauza" in text or "zatrzymaj muzykę" in text:
        pyautogui.press("playpause")
        return "Pauza."

    return None
