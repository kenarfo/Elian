import argparse
import asyncio
from dataclasses import dataclass

from bleak import BleakClient, BleakScanner

from VizziniBluetooth import parse_status_frame


TARGET_NAME = "RGB smart bulb"
TARGET_SERVICE = "0000a100-0000-1000-8000-00805f9b34fb"
STATUS_CHARACTERISTIC = "0000a102-1115-1000-0001-617573746f6d"

DEVICE_INFO_CHARACTERISTICS = {
    "00002a29-0000-1000-8000-00805f9b34fb": "manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "model",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial",
    "00002a28-0000-1000-8000-00805f9b34fb": "software",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware",
    "00002a27-0000-1000-8000-00805f9b34fb": "hardware",
}


@dataclass
class Candidate:
    address: str
    name: str
    rssi: int
    service_uuids: list[str]
    manufacturer_ids: list[int]

    @property
    def score(self):
        value = self.rssi
        if self.name.casefold() == TARGET_NAME.casefold():
            value += 100
        if TARGET_SERVICE in self.service_uuids:
            value += 100
        return value


async def scan(timeout):
    discovered = await BleakScanner.discover(
        timeout=timeout,
        return_adv=True,
    )

    candidates = []
    for device, advertisement in discovered.values():
        name = advertisement.local_name or device.name or "(no-name)"
        service_uuids = [uuid.lower() for uuid in advertisement.service_uuids]
        candidates.append(
            Candidate(
                address=device.address,
                name=name,
                rssi=advertisement.rssi,
                service_uuids=service_uuids,
                manufacturer_ids=sorted(advertisement.manufacturer_data),
            )
        )

    return sorted(candidates, key=lambda item: item.score, reverse=True)


def print_candidates(candidates):
    for candidate in candidates:
        marker = " <target>" if candidate.score >= 100 else ""
        print(
            f"{candidate.name} | {candidate.address} | "
            f"RSSI={candidate.rssi} | services={candidate.service_uuids} | "
            f"manufacturer_ids={candidate.manufacturer_ids}{marker}"
        )


async def inspect_services(address, watch_seconds=0):
    print(f"Connecting to {address} for read-only GATT discovery...")
    async with BleakClient(address, timeout=20.0) as client:
        print(f"Connected: {client.is_connected}")
        for service in client.services:
            print(f"SERVICE {service.uuid} | {service.description}")
            for characteristic in service.characteristics:
                properties = ",".join(characteristic.properties)
                print(
                    f"  CHAR {characteristic.uuid} | "
                    f"properties={properties} | {characteristic.description}"
                )

        print("Read-only values:")
        for uuid, label in DEVICE_INFO_CHARACTERISTICS.items():
            try:
                value = await client.read_gatt_char(uuid)
                text = value.decode("utf-8", errors="replace").rstrip("\x00")
                print(f"  {label}={text}")
            except Exception as error:
                print(f"  {label}=<unavailable: {error}>")

        try:
            status = await client.read_gatt_char(STATUS_CHARACTERISTIC)
            print(f"  status_hex={status.hex()}")
        except Exception as error:
            print(f"  status_hex=<unavailable: {error}>")

        if watch_seconds > 0:
            def on_status(_, data):
                try:
                    update = parse_status_frame(data)
                    print(
                        f"  notification {update.attribute}: "
                        f"{update.current} -> {update.target} "
                        f"({update.raw.hex()})",
                        flush=True,
                    )
                except ValueError:
                    print(
                        f"  notification unknown: {bytes(data).hex()}",
                        flush=True,
                    )

            print(
                f"Watching status notifications for {watch_seconds:g} seconds...",
                flush=True,
            )
            await client.start_notify(STATUS_CHARACTERISTIC, on_status)
            await asyncio.sleep(watch_seconds)
            await client.stop_notify(STATUS_CHARACTERISTIC)


async def main():
    parser = argparse.ArgumentParser(
        description="Discover the Vizzini BLE light without sending commands."
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--address")
    parser.add_argument("--watch", type=float, default=0.0)
    args = parser.parse_args()

    candidates = await scan(args.timeout)
    print_candidates(candidates)

    if args.scan_only:
        return

    target_address = args.address
    if not target_address:
        target = next(
            (candidate for candidate in candidates if candidate.score >= 100),
            None,
        )
        if target is None:
            raise RuntimeError("Vizzini BLE target was not discovered.")
        target_address = target.address

    await inspect_services(target_address, args.watch)


if __name__ == "__main__":
    asyncio.run(main())
