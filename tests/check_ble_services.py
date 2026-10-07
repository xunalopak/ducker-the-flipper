"""Inspect the remembered Flipper's GATT services without pairing or sending text."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from trans_gui import load_last_device
from trans_client import FLIPPER_SERVICE_UUID, FLIPPER_RX_CHAR_UUID, FLIPPER_TX_CHAR_UUID


async def main():
    remembered = load_last_device()
    assert remembered, "No remembered Flipper. Connect once with the GUI first."
    if sys.platform == "win32":
        device = BLEDevice(remembered["address"], "Remembered Flipper", None)
    else:
        device = await BleakScanner.find_device_by_address(remembered["address"], timeout=12)
        assert device, "Remembered Flipper is not advertising."
    async with BleakClient(device, pair=False, timeout=30,
                           winrt={"use_cached_services": False}) as client:
        print("Bluetooth link established:", client.is_connected)
        for service in client.services:
            print("Service:", service.uuid)
            for char in service.characteristics:
                print("  Characteristic:", char.uuid, ",".join(char.properties))
        print("Expected serial service:", client.services.get_service(FLIPPER_SERVICE_UUID) is not None)
        print("Serial RX present:", client.services.get_characteristic(FLIPPER_RX_CHAR_UUID) is not None)
        print("Serial TX present:", client.services.get_characteristic(FLIPPER_TX_CHAR_UUID) is not None)
        if "--contention" in sys.argv:
            async with BleakClient(device, pair=False, timeout=30,
                                   winrt={"use_cached_services": False}) as second:
                print("Second simultaneous client serial service:",
                      second.services.get_service(FLIPPER_SERVICE_UUID) is not None)
        print("No GATT writes, text transfer or USB execution requested.")


if __name__ == "__main__":
    asyncio.run(main())
