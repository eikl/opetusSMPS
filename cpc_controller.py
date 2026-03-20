"""CPC controller module (renamed from `cpc.py`).

Provides a simple CPC API used by the UI and measurement code:
 - start_cpc()
 - get_concentration() -> float

Internally it uses `hardware.cpc.device` which defaults to a simulator.
The module runs a single background thread that is the sole reader of the
CPC serial device.  All other code reads the latest cached value via
get_concentration(), which never touches the serial port.
"""
from hardware.cpc import device as cpc_device
from config import get_float
import hardware.cpc as _cpc_hw
import threading
import time

UPDATE_INTERVAL = get_float('CPC_SAMPLE_INTERVAL', 1.0)

_latest_value = None
_value_lock = threading.Lock()

def _cpc_loop():
    """Background thread — sole owner of CPC serial I/O."""
    global _latest_value
    while True:
        try:
            # allow the adapter to advance its internal model if it supports step()
            try:
                _cpc_hw.device.step(UPDATE_INTERVAL)
            except Exception:
                pass
            v = _cpc_hw.device.get_concentration()
            with _value_lock:
                _latest_value = v
        except Exception:
            pass
        time.sleep(UPDATE_INTERVAL)

def start_cpc():
    global _cpc_thread
    if globals().get('_cpc_thread') is not None and _cpc_thread.is_alive():
        return
    _cpc_thread = threading.Thread(target=_cpc_loop, daemon=True)
    _cpc_thread.start()

def get_concentration():
    """Return the latest concentration as float, or None if not yet available.

    This only reads a cached value — it never performs serial I/O.
    """
    with _value_lock:
        v = _latest_value
    if v is None:
        return None
    return float(v)


def reconnect():
    """Re-read config and reconnect the CPC device."""
    _cpc_hw.reconnect()
