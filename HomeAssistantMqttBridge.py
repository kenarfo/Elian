import atexit
import colorsys
import json
import queue
import sys
import threading
import time

import paho.mqtt.client as mqtt

from Config import config, get_secret
from VizziniBluetooth import (
    POWER_STATUS_QUERY,
    build_brightness_command,
    build_color_temperature_command,
    build_hs_color_command,
    build_power_command,
    build_rgb_color_command,
    control_light_sync,
)


DISCOVERY_TOPIC = (
    "homeassistant/light/elian_vizzini_zbek37/config"
)
COMMAND_TOPIC = "elian/vizzini/set"
STATE_TOPIC = "elian/vizzini/state"
AVAILABILITY_TOPIC = "elian/vizzini/availability"
HOME_ASSISTANT_STATUS_TOPIC = "homeassistant/status"

MIN_KELVIN = 2700
MAX_KELVIN = 6500
WINDOWS_MUTEX_NAME = "Local\\ElianVizziniMqttBridge"


def _acquire_instance_mutex():
    if sys.platform != "win32":
        return None

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    create_mutex.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = create_mutex(None, False, WINDOWS_MUTEX_NAME)

    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        raise RuntimeError("Most Home Assistant MQTT już działa.")

    return handle


def _release_instance_mutex(handle):
    if handle is None or sys.platform != "win32":
        return

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(handle)


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def build_discovery_payload():
    return {
        "name": "Vizzini",
        "unique_id": "elian_vizzini_zbek37",
        "default_entity_id": "light.vizzini",
        "schema": "json",
        "command_topic": COMMAND_TOPIC,
        "state_topic": STATE_TOPIC,
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "brightness": True,
        "supported_color_modes": [
            "rgb",
            "color_temp",
        ],
        "color_temp_kelvin": True,
        "min_kelvin": MIN_KELVIN,
        "max_kelvin": MAX_KELVIN,
        "optimistic": False,
        "qos": 1,
        "device": {
            "identifiers": ["elian_vizzini_zbek37"],
            "name": "Inspire Vizzini D56",
            "manufacturer": "ADEO",
            "model": "ZBEK-37",
            "sw_version": "2.12.05",
        },
        "origin": {
            "name": "Elian",
            "sw_version": "0.9",
        },
    }


def build_ble_commands(payload):
    if not isinstance(payload, dict):
        raise ValueError("Polecenie MQTT musi być obiektem JSON.")

    state = str(payload.get("state", "")).upper()

    if state == "OFF":
        return (build_power_command(False),)

    has_settings = any(
        key in payload
        for key in ("brightness", "color", "color_temp")
    )
    commands = []

    if state == "ON" or has_settings:
        commands.append(build_power_command(True))

    if "brightness" in payload:
        brightness = _clamp(
            int(round(float(payload["brightness"]))),
            0,
            255,
        )

        if brightness == 0:
            return (build_power_command(False),)

        commands.append(
            build_brightness_command(brightness * 100 / 255)
        )

    if "color_temp" in payload:
        kelvin = _clamp(
            int(round(float(payload["color_temp"]))),
            MIN_KELVIN,
            MAX_KELVIN,
        )
        commands.append(build_color_temperature_command(kelvin))

    color = payload.get("color")

    if isinstance(color, dict):
        if all(key in color for key in ("r", "g", "b")):
            commands.append(
                build_rgb_color_command(
                    color["r"],
                    color["g"],
                    color["b"],
                )
            )
        elif all(key in color for key in ("h", "s")):
            commands.append(
                build_hs_color_command(
                    color["h"],
                    float(color["s"]) / 100,
                )
            )

    if not commands:
        raise ValueError("Polecenie MQTT nie zawiera obsługiwanej zmiany.")

    return tuple(commands)


class VizziniMqttBridge:
    def __init__(
        self,
        bridge_config,
        password,
        client_factory=None,
        light_controller=control_light_sync,
    ):
        self.config = dict(bridge_config)
        self.password = password
        self.light_controller = light_controller
        self.address = self.config.get(
            "bluetooth_address",
            config.get("smart_home", {}).get(
                "bluetooth_address",
                "",
            ),
        ).strip()
        self.scan_interval = max(
            15,
            int(self.config.get("scan_interval", 60)),
        )
        self.retry_interval = max(
            1,
            int(self.config.get("retry_interval", 5)),
        )
        self._state = {
            "state": "OFF",
            "brightness": 255,
            "color_mode": "color_temp",
            "color_temp": 4000,
        }
        self._state_lock = threading.Lock()
        self._connected = threading.Event()
        self._running = threading.Event()
        self._sync_pending = threading.Event()
        self._sync_lock = threading.Lock()
        self._commands = queue.Queue()
        self._worker = None
        self._instance_mutex = None
        self._ble_fail_log_at = 0.0
        self._ble_fail_count = 0
        self._ble_backoff_until = 0.0

        factory = client_factory or mqtt.Client
        self.client = factory(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="elian-vizzini-bridge",
            protocol=mqtt.MQTTv311,
        )
        self.client.username_pw_set(
            self.config.get("username", "elian_bridge"),
            password,
        )
        self.client.will_set(
            AVAILABILITY_TOPIC,
            payload="offline",
            qos=1,
            retain=True,
        )
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.suppress_exceptions = True
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self):
        if self._running.is_set():
            return

        host = self.config.get("host", "").strip()

        if not host:
            raise ValueError("Brak adresu brokera MQTT.")

        if not self.password:
            raise ValueError("Brak hasła MQTT.")

        self._instance_mutex = _acquire_instance_mutex()
        self._running.set()
        self._worker = threading.Thread(
            target=self._run_worker,
            name="VizziniMQTT",
            daemon=True,
        )
        self._worker.start()

        try:
            self.client.connect_async(
                host,
                int(self.config.get("port", 1883)),
                keepalive=60,
            )
            self.client.loop_start()
        except Exception:
            self._running.clear()
            self._commands.put(None)
            self._worker.join(timeout=5)
            _release_instance_mutex(self._instance_mutex)
            self._instance_mutex = None
            raise

    def stop(self):
        if not self._running.is_set():
            return

        self._running.clear()
        self._commands.put(None)

        if self._connected.is_set():
            publication = self.client.publish(
                AVAILABILITY_TOPIC,
                "offline",
                qos=1,
                retain=True,
            )

            try:
                publication.wait_for_publish(timeout=2)
            except (RuntimeError, ValueError):
                pass

        self.client.disconnect()
        self.client.loop_stop()

        if self._worker is not None:
            self._worker.join(timeout=5)

        self._connected.clear()
        _release_instance_mutex(self._instance_mutex)
        self._instance_mutex = None

    def wait_until_connected(self, timeout=10):
        return self._connected.wait(timeout)

    def _on_connect(
        self,
        client,
        _userdata,
        _flags,
        reason_code,
        _properties,
    ):
        if reason_code.is_failure:
            print("Błąd połączenia MQTT:", reason_code)
            return

        self._connected.set()
        client.subscribe(
            [
                (COMMAND_TOPIC, 1),
                (HOME_ASSISTANT_STATUS_TOPIC, 0),
            ]
        )
        self._publish_discovery()
        self._publish_availability("online")
        self._request_sync()
        print("Most Home Assistant MQTT jest połączony.")

    def _on_disconnect(
        self,
        _client,
        _userdata,
        _disconnect_flags,
        _reason_code,
        _properties,
    ):
        self._connected.clear()

        if self._running.is_set():
            print("Most Home Assistant MQTT rozłączony:", _reason_code)

    def _on_message(self, _client, _userdata, message):
        try:
            text = message.payload.decode("utf-8")

            if message.topic == HOME_ASSISTANT_STATUS_TOPIC:
                if text.strip().lower() == "online":
                    self._publish_discovery()
                    self._publish_availability("online")
                    self._request_sync()
                return

            if message.topic != COMMAND_TOPIC:
                return

            payload = json.loads(text)

            if not isinstance(payload, dict):
                raise ValueError("Polecenie nie jest obiektem JSON.")

            self._commands.put(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            print("Nieprawidłowe polecenie MQTT:", error)

    def _run_worker(self):
        next_sync = time.monotonic() + self.scan_interval

        while self._running.is_set():
            timeout = max(0, next_sync - time.monotonic())

            try:
                payload = self._commands.get(timeout=timeout)
            except queue.Empty:
                if not self._connected.is_set():
                    next_sync = time.monotonic() + self.scan_interval
                    continue

                payload = {"_sync": True}

            if payload is None:
                break

            is_sync = bool(payload.get("_sync"))
            operation_succeeded = True

            try:
                if is_sync:
                    self._sync_state()
                else:
                    self._execute(payload)
            except Exception as error:
                operation_succeeded = False
                self._publish_availability("offline")
                try:
                    now = time.monotonic()
                    msg = str(error)
                    # BLE "not found" spam when HA already owns the light — backoff.
                    if "Nie znaleziono lampy" in msg or "Bluetooth" in msg:
                        self._ble_fail_count = int(self._ble_fail_count or 0) + 1
                        if now < float(self._ble_backoff_until or 0):
                            pass
                        elif now - float(self._ble_fail_log_at or 0) >= 60.0:
                            self._ble_fail_log_at = now
                            print(
                                "Błąd mostu Vizzini MQTT (backoff):",
                                error,
                                f"(x{self._ble_fail_count})",
                            )
                            # Exponential-ish backoff up to 5 minutes.
                            delay = min(300.0, 15.0 * min(self._ble_fail_count, 12))
                            self._ble_backoff_until = now + delay
                        else:
                            pass
                    else:
                        print("Błąd mostu Vizzini MQTT:", error)
                except OSError:
                    # Windows console can raise Errno 22 during shutdown / redirect.
                    pass
            finally:
                if is_sync:
                    self._sync_pending.clear()

            next_sync = time.monotonic() + (
                self.scan_interval
                if operation_succeeded
                else max(self.retry_interval, 15.0 if self._ble_fail_count else self.retry_interval)
            )

    def _request_sync(self):
        with self._sync_lock:
            if self._sync_pending.is_set():
                return

            self._sync_pending.set()
            self._commands.put({"_sync": True})

    def _execute(self, payload):
        now = time.monotonic()
        if now < float(getattr(self, "_ble_backoff_until", 0) or 0):
            raise RuntimeError(
                "Vizzini BLE w backoff — HA entity jest źródłem prawdy."
            )
        commands = build_ble_commands(payload)
        self.light_controller(
            commands,
            address=self.address,
        )
        self._ble_fail_count = 0
        self._ble_backoff_until = 0.0
        self._update_state_from_command(payload)
        self._publish_availability("online")
        self._publish_state()

    def _sync_state(self):
        result = self.light_controller(
            (POWER_STATUS_QUERY,),
            address=self.address,
            timeout=15,
            attempts=1,
        )

        with self._state_lock:
            for update in result.updates:
                if update.attribute == "power":
                    self._state["state"] = (
                        "ON" if update.current else "OFF"
                    )
        self._publish_availability("online")
        self._publish_state()

    def _update_state_from_command(self, payload):
        with self._state_lock:
            state = str(payload.get("state", "")).upper()

            if state in ("ON", "OFF"):
                self._state["state"] = state

            if "brightness" in payload:
                brightness = _clamp(
                    int(round(float(payload["brightness"]))),
                    0,
                    255,
                )
                self._state["brightness"] = brightness
                self._state["state"] = (
                    "ON" if brightness > 0 else "OFF"
                )

            if "color_temp" in payload:
                self._state["color_mode"] = "color_temp"
                self._state["color_temp"] = _clamp(
                    int(round(float(payload["color_temp"]))),
                    MIN_KELVIN,
                    MAX_KELVIN,
                )
                self._state.pop("color", None)

            color = payload.get("color")

            if isinstance(color, dict):
                rgb = self._get_rgb_color(color)

                if rgb is not None:
                    self._state["color_mode"] = "rgb"
                    self._state["color"] = rgb
                    self._state.pop("color_temp", None)

    @staticmethod
    def _get_rgb_color(color):
        if all(key in color for key in ("r", "g", "b")):
            return {
                key: _clamp(int(round(float(color[key]))), 0, 255)
                for key in ("r", "g", "b")
            }

        if all(key in color for key in ("h", "s")):
            hue = float(color["h"]) % 360 / 360
            saturation = _clamp(float(color["s"]), 0, 100) / 100
            red, green, blue = colorsys.hsv_to_rgb(
                hue,
                saturation,
                1,
            )
            return {
                "r": round(red * 255),
                "g": round(green * 255),
                "b": round(blue * 255),
            }

        return None

    def _publish_discovery(self):
        self.client.publish(
            DISCOVERY_TOPIC,
            json.dumps(
                build_discovery_payload(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            qos=1,
            retain=True,
        )

    def _publish_availability(self, state):
        if self._connected.is_set():
            self.client.publish(
                AVAILABILITY_TOPIC,
                state,
                qos=1,
                retain=True,
            )

    def _publish_state(self):
        if not self._connected.is_set():
            return

        with self._state_lock:
            payload = dict(self._state)

            if "color" in payload:
                payload["color"] = dict(payload["color"])

        self.client.publish(
            STATE_TOPIC,
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=True,
        )


_BRIDGE = None
_BRIDGE_LOCK = threading.Lock()


def start_mqtt_bridge():
    global _BRIDGE

    bridge_config = config.get("mqtt_bridge", {})

    if not bridge_config.get("enabled", False):
        return None

    with _BRIDGE_LOCK:
        if _BRIDGE is None:
            _BRIDGE = VizziniMqttBridge(
                bridge_config,
                get_secret(
                    "mqtt_password",
                    env_var="ELIAN_MQTT_PASSWORD",
                ),
            )
            _BRIDGE.start()

    return _BRIDGE


def stop_mqtt_bridge():
    global _BRIDGE

    with _BRIDGE_LOCK:
        if _BRIDGE is not None:
            _BRIDGE.stop()
            _BRIDGE = None


atexit.register(stop_mqtt_bridge)
