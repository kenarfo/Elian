# ==========================
# ELIAN AI ASSISTANT
# Version 0.6
# Skills/Weather.py
# ==========================

import re
from urllib.parse import quote

import requests

from Config import config


# Domyślna lokalizacja
CITY = config.get("location", {}).get("city", "Lodz")


def _description(condition):

    return (
        condition.get("lang_pl", [{}])[0].get("value")
        or condition.get("weatherDesc", [{}])[0].get("value")
        or "brak opisu"
    )


def get_weather(city=None, day=0):

    city = (city or CITY).strip()

    if not city or len(city) > 80 or "\n" in city:

        city = CITY

    try:

        url = (
            "https://wttr.in/"
            + quote(city)
            + "?format=j1&lang=pl"
        )


        response = requests.get(
            url,
            timeout=5
        )

        response.raise_for_status()


        data = response.json()


        day = max(0, min(2, int(day)))

        if day > 0:

            forecast = data["weather"][day]
            hours = forecast.get("hourly", [])
            noon = next(
                (
                    hour
                    for hour in hours
                    if str(hour.get("time", "")) == "1200"
                ),
                hours[len(hours) // 2] if hours else {}
            )
            label = "Jutro" if day == 1 else "Pojutrze"
            description = _description(noon)
            minimum = forecast.get("mintempC", "?")
            maximum = forecast.get("maxtempC", "?")

            return (
                f"{city}: {label.lower()} będzie od {minimum} "
                f"do {maximum} stopni. {description}."
            )

        current = data["current_condition"][0]


        temperature = current["temp_C"]

        description = _description(current)

        wind = current["windspeedKmph"]


        return (
            f"{city}: teraz jest "
            f"{temperature} stopni. "
            f"Pogoda: {description}. "
            f"Wiatr {wind} kilometrów na godzinę."
        )


    except Exception:

        return (
            "Nie udało mi się pobrać pogody."
        )



def run(text):

    text = text.lower()


    weather_words = [
        "pogoda",
        "temperatura",
        "jaka jest pogoda",
        "ile jest stopni"
    ]


    for word in weather_words:

        if word in text:

            needs_routing = (
                "jutro" in text
                or "pojutrze" in text
                or re.search(
                    r"\b(?:pogoda|temperatura|stopni)\s+(?:jest\s+)?w\s+[a-ząćęłńóśźż-]+",
                    text,
                )
            )

            if needs_routing:

                return None

            return get_weather()


    return None
