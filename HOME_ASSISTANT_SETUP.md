# Lampa Vizzini w Elianie

## Sterowanie Bluetooth

Elian steruje lampą Inspire Vizzini bezpośrednio przez Bluetooth komputera.
Nie jest potrzebna Box ENKI ani encja `light.*` w Home Assistant.

Ustawienia znajdują się w `Data/config.json`:

```json
"smart_home": {
    "enabled": true,
    "system": "vizzini_bluetooth",
    "device_name": "lampa",
    "bluetooth_address": "28:DB:A7:16:D5:39"
}
```

Biblioteka `bleak` jest zapisana w `Requirements.txt`. Po przeniesieniu projektu
na inny komputer zainstaluj zależności i zmień `bluetooth_address` na adres lampy.
Adres można znaleźć poleceniem:

```powershell
python BluetoothProbe.py --scan-only
```

Podczas sterowania aplikacja ENKI w telefonie powinna być zamknięta. Lampa
przyjmuje tylko jedno aktywne połączenie Bluetooth naraz.

## Obsługiwane komendy

- `włącz lampę`
- `wyłącz lampę`
- `ustaw jasność lampy na 50 procent`
- `ustaw lampę na czerwony`
- `ustaw ciepłe światło`
- `ustaw neutralne światło`
- `ustaw zimne światło`
- `jaki jest stan lampy`

## Home Assistant przez most MQTT

Lampa nie potrzebuje ENKI Boxa. Moduł `HomeAssistantMqttBridge.py` łączy się z
nią przez Bluetooth i publikuje w Home Assistant encję `light.vizzini` przez
MQTT Discovery. Encja obsługuje:

- włączanie i wyłączanie,
- jasność,
- kolory RGB,
- temperaturę bieli od 2700 K do 6500 K,
- odczyt stanu co 60 sekund.

Do działania potrzebny jest oficjalny dodatek Mosquitto Broker w Home Assistant
OS, integracja MQTT oraz osobne konto MQTT dla Eliana. Broker nasłuchuje na
porcie `1883` w sieci lokalnej i nie zezwala na połączenia anonimowe.

Konfiguracja mostu znajduje się w `Data/config.json`:

```json
"mqtt_bridge": {
    "enabled": true,
    "host": "192.168.0.31",
    "port": 1883,
    "username": "elian_bridge",
    "bluetooth_address": "28:DB:A7:16:D5:39",
    "scan_interval": 60,
    "retry_interval": 5
}
```

Hasło MQTT należy zapisać jako `mqtt_password`, a token Home Assistant jako
`home_assistant_token` w ignorowanym przez Git pliku `Data/secrets.json`.
Alternatywnie można użyć zmiennych `ELIAN_MQTT_PASSWORD` i `ELIAN_HA_TOKEN`.

Po potwierdzeniu działania encji ustaw:

```json
"smart_home": {
    "enabled": true,
    "system": "home_assistant",
    "url": "http://192.168.0.31",
    "entity_id": "light.vizzini",
    "device_name": "lampa"
}
```

W bieżącej konfiguracji most jest włączony, a Elian steruje encją
`light.vizzini` przez REST API Home Assistanta. Most tłumaczy polecenia MQTT na
sprawdzone komendy Bluetooth lampy.
