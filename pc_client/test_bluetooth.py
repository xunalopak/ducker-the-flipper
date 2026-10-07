"""Run without Bluetooth hardware: python -m unittest discover -s pc_client."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock, patch

import trans_gui as gui


class FakeClient:
    failure = None
    capacity = 4096
    instances = []

    def __init__(self, device, **options):
        self.device = device
        self.options = options
        self.is_connected = False
        self.closed = False
        self.writes = []
        self.services = [SimpleNamespace(uuid=gui.FLIPPER_SERVICE_UUID)]
        self.instances.append(self)

    async def connect(self):
        self.is_connected = True
        if self.failure == "connect":
            raise RuntimeError("pairing rejected")
        if self.failure == "wait":
            await asyncio.Event().wait()
        if self.failure == "service":
            self.services = []

    async def start_notify(self, uuid, callback):
        # Independent firmware UUID: using the flow-control channel must fail this test.
        if uuid != "19ed82ae-ed21-4c9d-4145-228e61fe0000":
            raise RuntimeError("Subscribed to flow control instead of serial TX")
        if self.failure == "notify":
            raise RuntimeError("notification subscription failed")
        self.notify = callback

    async def write_gatt_char(self, uuid, data, response):
        self.writes.append(bytes(data))
        if data == b"TTF?\n":
            if self.failure == "handshake":
                return
            self.notify(None, bytearray(f"READY:1:{self.capacity}\n".encode()))
        else:
            self.notify(None, bytearray(b"RECV\n"))

    async def disconnect(self):
        self.closed = True
        self.is_connected = False
        self.options["disconnected_callback"](self)


class BluetoothTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_known_windows_device_without_advertising(self):
        events = []
        worker = gui.BleWorker(lambda *event: events.append(event))
        worker._loop.call_soon_threadsafe(worker._loop.stop)
        await asyncio.to_thread(worker._thread.join, 3)
        worker._loop.close()
        address = "00:00:00:00:00:01"
        FakeClient.failure = None
        with patch.object(gui.sys, "platform", "win32"), patch.object(gui, "BleakClient", FakeClient), patch.object(
            gui.BleakScanner, "find_device_by_address", new=AsyncMock(side_effect=AssertionError("Must not require advertising"))
        ) as scanner:
            await worker._connect(address, "Demo Flipper")
            old = worker._client
            self.assertEqual(old.device.address, address)
            # Check the real WinRT constructor, not only our fake client.
            if os.name == "nt":
                from bleak.backends.winrt.client import BleakClientWinRT
                native = BleakClientWinRT(old.device, timeout=60, disconnected_callback=None,
                                          winrt={"use_cached_services": False})
                self.assertEqual(native._device_info, 1)
            worker._devices[address] = old.device
            await worker._repair(address, "Demo Flipper")
            self.assertTrue(old.closed)
            self.assertIsNot(worker._client, old)
            self.assertEqual(worker._client.writes, [b"TTF?\n"])
            self.assertIn(("repairing", True), events)
            self.assertEqual(events[-1], ("repairing", False))
            scanner.assert_not_called()
            worker._on_disconnected(old)
            self.assertIsNotNone(worker._client)
            await worker._disconnect()

            # Cancelling repair while the old session is closing still waits for cleanup.
            await worker._connect(address, "Demo Flipper")
            closing_client = worker._client
            original_disconnect = closing_client.disconnect
            closing_started = asyncio.Event()

            async def slow_disconnect():
                closing_started.set()
                await asyncio.sleep(0.05)
                await original_disconnect()

            closing_client.disconnect = slow_disconnect
            repair = asyncio.create_task(worker._repair(address, "Demo Flipper"))
            await closing_started.wait()
            await worker._disconnect()
            self.assertTrue(repair.cancelled())
            self.assertTrue(closing_client.closed)
            self.assertIsNone(worker._client)

            # A running repair can be cancelled using the existing Disconnect button.
            FakeClient.failure = "wait"
            repair = asyncio.create_task(worker._repair(address, "Demo Flipper"))
            await asyncio.sleep(0)
            await worker._disconnect()
            self.assertTrue(repair.cancelled())
            self.assertIsNone(worker._client)
            self.assertIsNone(worker._repair_task)
            self.assertIsNone(worker._connect_task)
            FakeClient.failure = None

    async def test_repair_safety_and_missing_protocol(self):
        events = []
        worker = gui.BleWorker(lambda *event: events.append(event))
        worker._loop.call_soon_threadsafe(worker._loop.stop)
        await asyncio.to_thread(worker._thread.join, 3)
        worker._loop.close()
        worker._awaiting_result = True
        await worker._repair("address", "Demo Flipper")
        self.assertIn(("repair_error", "busy"), events)
        self.assertIsNone(worker._client)
        client = FakeClient(object(), disconnected_callback=worker._on_disconnected)
        client.is_connected = True
        worker._client = client
        worker._repair_task = asyncio.current_task()
        await worker._send("must not send")
        await worker._execute()
        await worker._set_layout("fr-FR.kl")
        self.assertEqual(client.writes, [])
        worker._repair_task = None
        worker._awaiting_result = False
        await worker._disconnect()
        worker._devices["address"] = object()
        FakeClient.failure = "handshake"
        try:
            with patch.object(gui, "BleakClient", FakeClient):
                await worker._connect("address", "Demo Flipper")
            self.assertIsNone(worker._client)
            self.assertTrue(FakeClient.instances[-1].closed)
            self.assertNotIn(("connected", "Demo Flipper"), events)
            self.assertTrue(any(kind == "diagnostic" and "Bluetooth is connected" in str(payload)
                                for kind, payload in events))
        finally:
            FakeClient.failure = None

    async def test_layout_responses_and_transfer_exclusion(self):
        events = []
        worker = gui.BleWorker(lambda *event: events.append(event))
        worker._loop.call_soon_threadsafe(worker._loop.stop)
        await asyncio.to_thread(worker._thread.join, 3)
        worker._loop.close()
        worker._notifications = gui.NotificationLines()
        reply = b"LAYOUT:fr-FR\n"
        writes = []

        async def write(uuid, data, response):
            writes.append(data)
            worker._on_notify(None, bytearray(reply))

        client = SimpleNamespace(is_connected=True, write_gatt_char=write)
        worker._client = client
        await worker._set_layout("fr-FR.kl")
        self.assertEqual(writes, [b"TTFLAYOUT fr-FR.kl\n"])
        self.assertIn(("layout_status", "fr-FR"), events)
        reply = b"ERR:LAYOUT\n"
        await worker._set_layout("de-DE.kl")
        self.assertIn(("layout_error", "ERR:LAYOUT"), events)
        worker._awaiting_result = True
        await worker._set_layout("QWERTY US")
        self.assertEqual(len(writes), 2)
        worker._awaiting_result = False
        worker._layout_task = asyncio.current_task()
        await worker._send("must not be written")
        self.assertEqual(len(writes), 2)
        worker._on_notify(None, bytearray(b"LAYOUT:fr-FR\nRECV\nOK\n"))
        self.assertIn(("notify", "RECV"), events)
        self.assertIn(("notify", "OK"), events)
        worker._layout_event.clear()
        worker._on_disconnected(client)
        self.assertTrue(worker._layout_event.is_set())
        self.assertEqual(worker._layout_error, "Bluetooth connection lost.")

    async def test_connection_lifecycle(self):
        events = []
        worker = gui.BleWorker.__new__(gui.BleWorker)
        worker._emit = lambda *event: events.append(event)
        worker._client = None
        worker._connect_task = None
        worker._send_task = None
        device = object()
        worker._devices = {"address": device}
        with patch.object(gui, "BleakClient", FakeClient):
            for failure in ("connect", "service", "notify", None):
                FakeClient.failure = failure
                events.clear()
                await worker._connect("address", "Flipper")
                client = FakeClient.instances[-1]
                self.assertIs(client.device, device)
                self.assertTrue(client.options["pair"])
                self.assertFalse(client.options["winrt"]["use_cached_services"])
                if failure:
                    self.assertTrue(client.closed)
                    self.assertIsNone(worker._client)
                    self.assertNotIn(("connected", "Flipper"), events)
                else:
                    self.assertIn(("connected", "Flipper"), events)
                    self.assertIn(("capacity", 4096), events)
                    # A late callback from a previous connection must not clear this one.
                    worker._on_disconnected(FakeClient.instances[-2])
                    self.assertIs(worker._client, client)
                    await worker._disconnect()
                    self.assertTrue(client.closed)
                    self.assertNotIn(("log", "🔌  Link lost (device disconnected)."), events)
                self.assertIn(("disconnected", None), events)

            FakeClient.failure = "wait"
            task = asyncio.create_task(worker._connect("address", "Flipper"))
            await asyncio.sleep(0)
            await worker._disconnect()
            self.assertTrue(task.cancelled())
            self.assertTrue(FakeClient.instances[-1].closed)
            self.assertIsNone(worker._connect_task)
            FakeClient.failure = None
            await worker._connect("address", "Flipper")
            self.assertIsNotNone(worker._client)
            await worker._disconnect()
            FakeClient.capacity = 255
            try:
                await worker._connect("address", "Old Flipper")
                self.assertEqual(worker._max_text_bytes, 255)
                await worker._disconnect()
            finally:
                FakeClient.capacity = 4096

    async def test_gui_disconnect_states(self):
        app = SimpleNamespace(
            _connected=False, _busy=True, connect_btn=Mock(), disconnect_btn=Mock(),
            send_btn=Mock(), execute_btn=Mock(), scan_btn=Mock(), device_menu=Mock(),
            _set_status=Mock(), _worker=Mock(),
            _dev_map={}, _pending_text=None,
        )
        gui.App._handle_event(app, "connected", "Flipper")
        self.assertTrue(app._connected)
        app.disconnect_btn.configure.assert_called_with(state="normal")
        app.connect_btn.configure.assert_called_with(state="disabled")
        gui.App._on_disconnect_click(app)
        app._worker.disconnect.assert_called_once()
        app.send_btn.configure.assert_called_with(state="disabled")
        gui.App._handle_event(app, "disconnected", None)
        self.assertFalse(app._connected)
        app.disconnect_btn.configure.assert_called_with(state="disabled")
        app.connect_btn.configure.assert_called_with(state="normal")

    async def test_shutdown_waits_for_disconnect(self):
        worker = gui.BleWorker(lambda *_: None)
        closed = []

        async def disconnect():
            await asyncio.sleep(0.05)
            closed.append(True)

        worker._client = SimpleNamespace(disconnect=disconnect)
        worker.shutdown()
        await asyncio.to_thread(worker._thread.join, 3)
        self.assertFalse(worker._thread.is_alive())
        self.assertEqual(closed, [True])
        worker._loop.close()


if __name__ == "__main__":
    unittest.main()
