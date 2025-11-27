"""CPC controller module (renamed from `cpc.py`).

Provides a simple CPC API used by the UI and measurement code:
 - start_cpc()
 - get_concentration() -> float

Internally it uses `hardware.cpc.device` which defaults to a simulator.
The module runs a small background thread that calls `device.step()` at the
configured sample interval when available.
"""
from hardware.cpc import device as cpc_device
from config import get_float
import threading
import time

UPDATE_INTERVAL = get_float('CPC_SAMPLE_INTERVAL', 1.0)

def _cpc_loop():
    while True:
        try:
            # allow the adapter to advance its internal model if it supports step()
            try:
                cpc_device.step(UPDATE_INTERVAL)
            except Exception:
                pass
        except Exception:
            pass
        time.sleep(UPDATE_INTERVAL)

def start_cpc():
    global _cpc_thread
    if globals().get('_cpc_thread') is not None and _cpc_thread.is_alive():
        return
    _cpc_thread = threading.Thread(target=_cpc_loop, daemon=True)
    _cpc_thread.start()

def get_concentration() -> float:
    return float(cpc_device.get_concentration())
