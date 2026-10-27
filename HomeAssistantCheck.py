import sys

from HomeAssistant import get_headers, get_registry, test_connection
from Config import config


def main():
    if get_headers() is None:
        print(
            "Brak tokenu Home Assistant. "
            "Ustaw ELIAN_HA_TOKEN albo dodaj "
            "go do Data/secrets.json."
        )
        return 1

    if not test_connection():
        print("Nie udało się uwierzytelnić w Home Assistant.")
        return 1

    print("Połączenie z Home Assistant: OK")
    entities = get_registry().refresh()
    if not entities:
        print("Brak obsługiwanych encji w Home Assistant.")
        return 0

    selected = config.get("smart_home", {}).get("entity_id", "")
    print("Dostępne urządzenia:")
    for entity in entities:
        marker = " [wybrane]" if entity.entity_id == selected else ""
        extra = ", ".join(entity.color_modes) or entity.state
        print(
            f"- {entity.display_name()}: {entity.entity_id} "
            f"[{entity.domain}: {extra}]{marker}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
