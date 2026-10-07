"""Check the real GUI worker handshake without creating Tk or sending/executing text."""
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pc_client"))
from trans_gui import BleWorker, load_last_device


remembered = load_last_device()
assert remembered, "No remembered Flipper. Connect once with the GUI first."
events = queue.Queue()
worker = BleWorker(lambda *event: events.put(event))
try:
    worker.connect(remembered["address"], "Remembered Flipper")
    while True:
        kind, payload = events.get(timeout=75)
        if kind == "capacity":
            print("TTF1 capacity:", payload)
        elif kind == "layout_status":
            print("Keyboard layout response received.")
        elif kind == "connected":
            print("Real GUI worker connection and TTF1 handshake passed; no text or HID execution.")
            break
        elif kind == "diagnostic":
            detail = str(payload).replace(remembered["address"], "<device>")
            print("Connection diagnostic:", type(payload).__name__, detail)
        elif kind == "disconnected":
            raise SystemExit("Connection was not verified.")
finally:
    worker.shutdown()
    worker._thread.join(10)
    assert not worker._thread.is_alive(), "Bluetooth worker did not close cleanly."
    worker._loop.close()
