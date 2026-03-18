"""HV controller service.

Provides a small API for the HV control loop and adapter. API mirrors the
previous `high_voltage.py` but with clearer naming.

Exports:
 - start_hv()
 - set_hv_setpoint(voltage)
 - get_hv_setpoint()
 - get_hv_voltage()

Internally delegates to `hardware.hv.device` for actual hardware/simulator.
"""
from hardware.hv import device as hv_device
from config import get_float
import hardware.hv as _hv_hw
import time
import threading

# control/update loop interval (can be configured via HV_UPDATE_INTERVAL)
UPDATE_INTERVAL = float(get_float('HV_UPDATE_INTERVAL', 0.05))

# lock to protect access to the setpoint
_hv_lock = threading.Lock()
# default setpoint (in volts)
_hv_setpoint = 10
# last applied setpoint (to detect changes)
_hv_last_applied = None
# flag to trigger immediate send
_hv_send_requested = False


def set_hv_setpoint(voltage: float, send_now: bool = True):
    """Set HV setpoint and optionally send command immediately.
    
    Args:
        voltage: Target voltage in volts
        send_now: If True, triggers immediate send. If False, only updates setpoint.
    """
    global _hv_setpoint, _hv_send_requested
    with _hv_lock:
        _hv_setpoint = float(voltage)
        if send_now:
            _hv_send_requested = True


def get_hv_setpoint() -> float:
    with _hv_lock:
        return float(_hv_setpoint)


def get_hv_voltage() -> float:
    return float(_hv_hw.device.get_voltage())


def get_hv_status() -> dict | None:
    """Query the HV supply status register.

    Returns a dict with 'raw', 'value', 'bits' keys, or None on error.
    """
    try:
        return _hv_hw.device.get_status()
    except Exception as e:
        print(f'get_hv_status error: {e}')
        return None


def _hv_loop():
    global _hv_last_applied, _hv_send_requested
    while True:
        try:
            with _hv_lock:
                sp = _hv_setpoint
                send_req = _hv_send_requested
                _hv_send_requested = False
            
            # Only send if explicitly requested
            if send_req:
                _hv_hw.device.set_voltage(sp)
                _hv_last_applied = sp
                print(f"HV setpoint applied: {sp} V")
        except Exception as e:
            print(f"Error in HV control loop: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(UPDATE_INTERVAL)


def start_hv():
    """Start background threads required for HV control/simulation.

    Safe to call multiple times; idempotent.
    """
    global _hv_thread
    if globals().get('_hv_thread') is not None and _hv_thread.is_alive():
        return
    _hv_thread = threading.Thread(target=_hv_loop, daemon=True)
    _hv_thread.start()


def reconnect():
    """Re-read config and reconnect the HV device."""
    _hv_hw.reconnect()
