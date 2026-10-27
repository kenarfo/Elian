import asyncio
import atexit
import colorsys
import math
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError


TARGET_NAME = "RGB smart bulb"
TARGET_SERVICE = "0000a100-0000-1000-8000-00805f9b34fb"
COMMAND_CHARACTERISTIC = "0000a101-1115-1000-0001-617573746f6d"
STATUS_CHARACTERISTIC = "0000a102-1115-1000-0001-617573746f6d"

ATTRIBUTE_POWER = 0x10
ATTRIBUTE_BRIGHTNESS = 0x11
ATTRIBUTE_COLOR_TEMPERATURE = 0x12
ATTRIBUTE_COLOR = 0x13

COMMAND_OPCODE = 0x01
REPORT_OPCODE = 0x03
SATURATION_REPORT_OPCODE = 0x06
COLOR_COMMAND_OPCODE = 0x07
COLOR_REPORT_OPCODE = 0x09


@dataclass(frozen=True)
class StatusUpdate:
    attribute: str
    current: object
    target: object
    opcode: int
    raw: bytes


@dataclass(frozen=True)
class ControlResult:
    address: str
    updates: tuple[StatusUpdate, ...]


def _frame(attribute, opcode, payload=b""):
    payload = bytes(payload)

    if len(payload) > 255:
        raise ValueError("Vizzini payload is too long.")

    return (
        b"\x00\x00"
        + bytes((attribute, opcode, len(payload)))
        + payload
    )


INITIALIZE_COMMAND = _frame(
    0x20,
    COMMAND_OPCODE,
    (2).to_bytes(2, "big"),
)

POWER_STATUS_QUERY = b"\x00\x00\x10\x02"
BRIGHTNESS_STATUS_QUERY = b"\x00\x00\x11\x02"
COLOR_TEMPERATURE_STATUS_QUERY = b"\x00\x00\x12\x02"
COLOR_STATUS_QUERY = b"\x00\x00\x13\x0d"


def build_power_command(enabled):
    return _frame(
        ATTRIBUTE_POWER,
        COMMAND_OPCODE,
        bytes((int(bool(enabled)), 0, 0)),
    )


def build_brightness_command(percent):
    percent = float(percent)

    if not 0 <= percent <= 100:
        raise ValueError("Brightness must be between 0 and 100 percent.")

    raw = math.floor((percent * 255 / 100) + 0.5)

    return _frame(
        ATTRIBUTE_BRIGHTNESS,
        COMMAND_OPCODE,
        bytes((raw, 0, 0)),
    )


def build_color_temperature_command(kelvin):
    kelvin = int(kelvin)

    if not 2700 <= kelvin <= 6500:
        raise ValueError("Color temperature must be between 2700 and 6500 K.")

    mired = 1_000_000 // kelvin

    return _frame(
        ATTRIBUTE_COLOR_TEMPERATURE,
        COMMAND_OPCODE,
        mired.to_bytes(2, "big") + b"\x00\x00",
    )


def build_hs_color_command(hue_degrees, saturation):
    hue_degrees = float(hue_degrees) % 360
    saturation = float(saturation)

    if not 0 <= saturation <= 1:
        raise ValueError("Saturation must be between 0 and 1.")

    hue = math.floor((hue_degrees / 360 * 255) + 0.5)
    saturation_raw = math.floor((saturation * 255) + 0.5)
    saturation_raw = min(saturation_raw, 254)

    return _frame(
        ATTRIBUTE_COLOR,
        COLOR_COMMAND_OPCODE,
        bytes((hue, saturation_raw, 0, 0)),
    )


def build_rgb_color_command(red, green, blue):
    values = (int(red), int(green), int(blue))

    if any(value < 0 or value > 255 for value in values):
        raise ValueError("RGB values must be between 0 and 255.")

    hue, saturation, _ = colorsys.rgb_to_hsv(
        *(value / 255 for value in values)
    )

    return build_hs_color_command(hue * 360, saturation)


def parse_status_frame(data):
    frame = bytes(data)

    if len(frame) < 5 or frame[:2] != b"\x00\x00":
        raise ValueError("Invalid Vizzini status frame.")

    attribute_id = frame[2]
    opcode = frame[3]
    payload_length = frame[4]
    payload = frame[5:]

    if len(payload) != payload_length:
        raise ValueError("Invalid Vizzini status payload.")

    if attribute_id == ATTRIBUTE_POWER and opcode == REPORT_OPCODE:
        if payload_length != 2:
            raise ValueError("Invalid power payload.")
        current, target = (bool(value) for value in payload)
        attribute = "power"
    elif attribute_id == ATTRIBUTE_BRIGHTNESS and opcode == REPORT_OPCODE:
        if payload_length != 2:
            raise ValueError("Invalid brightness payload.")
        current, target = (
            round(value * 100 / 255)
            for value in payload
        )
        attribute = "brightness_percent"
    elif (
        attribute_id == ATTRIBUTE_COLOR_TEMPERATURE
        and opcode == REPORT_OPCODE
    ):
        if payload_length != 4:
            raise ValueError("Invalid color-temperature payload.")
        current = int.from_bytes(payload[:2], "big")
        target = int.from_bytes(payload[2:], "big")
        attribute = "color_temperature_mired"
    elif attribute_id == ATTRIBUTE_COLOR and opcode == REPORT_OPCODE:
        if payload_length != 2:
            raise ValueError("Invalid hue payload.")
        current, target = (
            round(value * 360 / 255)
            for value in payload
        )
        attribute = "hue_degrees"
    elif (
        attribute_id == ATTRIBUTE_COLOR
        and opcode == SATURATION_REPORT_OPCODE
    ):
        if payload_length != 2:
            raise ValueError("Invalid saturation payload.")
        current, target = (
            round(value * 100 / 255)
            for value in payload
        )
        attribute = "saturation_percent"
    elif attribute_id == ATTRIBUTE_COLOR and opcode == COLOR_REPORT_OPCODE:
        if payload_length != 2:
            raise ValueError("Invalid combined-color payload.")
        value = (
            round(payload[0] * 360 / 255),
            round(payload[1] * 100 / 255),
        )
        current = value
        target = value
        attribute = "color_hs"
    elif attribute_id == 0x20 and opcode == REPORT_OPCODE:
        if payload_length != 2:
            raise ValueError("Invalid protocol-version payload.")
        current = int.from_bytes(payload, "big")
        target = current
        attribute = "protocol_version"
    else:
        current = payload
        target = payload
        attribute = f"unknown_0x{attribute_id:02x}_0x{opcode:02x}"

    return StatusUpdate(
        attribute=attribute,
        current=current,
        target=target,
        opcode=opcode,
        raw=frame,
    )


async def _find_device(address, timeout):
    if address:
        device = await BleakScanner.find_device_by_address(
            address,
            timeout=min(timeout, 5.0),
        )
    else:
        device = await BleakScanner.find_device_by_filter(
            lambda found, advertisement: (
                (advertisement.local_name or found.name or "").casefold()
                == TARGET_NAME.casefold()
                or TARGET_SERVICE in (
                    uuid.lower()
                    for uuid in advertisement.service_uuids
                )
            ),
            timeout=timeout,
        )

    if device is None:
        raise RuntimeError("Nie znaleziono lampy Vizzini przez Bluetooth.")

    return device


async def _control_light_once(commands, target_device, timeout):
    updates = []

    def on_status(_, data):
        try:
            updates.append(parse_status_frame(data))
        except ValueError:
            pass

    client_options = {
        "timeout": timeout,
    }

    if sys.platform == "win32":
        client_options["winrt"] = {
            "use_cached_services": False,
        }

    async with BleakClient(target_device, **client_options) as client:
        notifications_started = False

        try:
            await client.start_notify(STATUS_CHARACTERISTIC, on_status)
            notifications_started = True
        except (BleakError, OSError):
            pass

        await client.write_gatt_char(
            COMMAND_CHARACTERISTIC,
            INITIALIZE_COMMAND,
            response=True,
        )
        await asyncio.sleep(0.15)

        for command in commands:
            await client.write_gatt_char(
                COMMAND_CHARACTERISTIC,
                command,
                response=True,
            )
            await asyncio.sleep(0.15)

        await asyncio.sleep(0.35)

        if notifications_started:
            try:
                await client.stop_notify(STATUS_CHARACTERISTIC)
            except (BleakError, OSError):
                pass

    return ControlResult(
        address=target_device.address,
        updates=tuple(updates),
    )


async def control_light(commands, address="", timeout=15.0, attempts=3):
    commands = tuple(bytes(command) for command in commands)

    if attempts < 1:
        raise ValueError("At least one connection attempt is required.")

    for attempt in range(attempts):
        try:
            target_device = await _find_device(address, timeout)

            return await _control_light_once(
                commands,
                target_device,
                timeout,
            )
        except (BleakError, OSError, RuntimeError, TimeoutError):
            if attempt == attempts - 1:
                raise

            await asyncio.sleep(1.5 * (attempt + 1))


class _BleLoopWorker:
    def __init__(self):
        self.loop = None
        self.ready = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="VizziniBLE",
            daemon=True,
        )
        self.thread.start()

        if not self.ready.wait(timeout=5):
            raise RuntimeError("Nie udało się uruchomić wątku Bluetooth.")

    def _run(self):
        if sys.platform == "win32":
            try:
                from bleak.backends.winrt.util import uninitialize_sta

                uninitialize_sta()
            except ImportError:
                pass

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()

        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)

            for task in pending:
                task.cancel()

            if pending:
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )

            self.loop.close()

    def submit(self, coroutine, timeout):
        if not self.thread.is_alive() or self.loop is None:
            coroutine.close()
            raise RuntimeError("Wątek Bluetooth nie działa.")

        future = asyncio.run_coroutine_threadsafe(
            coroutine,
            self.loop,
        )

        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError as error:
            future.cancel()
            raise TimeoutError(
                "Przekroczono czas sterowania lampą Bluetooth."
            ) from error

    def stop(self):
        if self.loop is not None and self.thread.is_alive():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=3)


_BLE_WORKER = None
_BLE_WORKER_LOCK = threading.Lock()


def _get_ble_worker():
    global _BLE_WORKER

    with _BLE_WORKER_LOCK:
        if _BLE_WORKER is None or not _BLE_WORKER.thread.is_alive():
            _BLE_WORKER = _BleLoopWorker()

        return _BLE_WORKER


def _stop_ble_worker():
    if _BLE_WORKER is not None:
        _BLE_WORKER.stop()


atexit.register(_stop_ble_worker)


def control_light_sync(commands, address="", timeout=15.0, attempts=3):
    scan_timeout = min(timeout, 5.0) if address else timeout
    wait_timeout = attempts * (timeout + scan_timeout + 5) + 5

    return _get_ble_worker().submit(
        control_light(commands, address, timeout, attempts),
        timeout=wait_timeout,
    )
