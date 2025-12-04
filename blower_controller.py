"""Blower controller service.

Provides a small API for blower control and simulation.

Exports:
 - start_blower()
 - set_setpoint(value)
 - get_setpoint()
 - get_parameter()
"""
from hardware.blowerdac import device as blowerdac_device
from hardware.dummy_devices import DummyBlowerDevice
from config import get_float, get_str
from simple_pid import PID
import threading
import time
import logging

UPDATE_INTERVAL = float(get_float('BLOWER_UPDATE_INTERVAL', 0.05))

# Check if we're using a dummy blower device
_is_dummy_blower = isinstance(blowerdac_device, DummyBlowerDevice)

# PID and setpoint
_blower_setpoint_lock = threading.Lock()
_blower_setpoint = float(get_float('BLOWER_DEFAULT_SETPOINT', 4.0))
_pid = PID(0.005, 0.03, 0, setpoint=_blower_setpoint)
_pid.output_limits = (0,5)
# whether to use the flowmeter reading as the PID process variable
_use_flow_pv = False
try:
    v = get_str('BLOWER_USE_FLOW_PV', '0')
    _use_flow_pv = str(v).strip().lower() in ('1', 'true', 'yes', 'on')
except Exception:
    _use_flow_pv = False

# attempt to locate a flowmeter device if configured; do not raise here — caller may inspect
_flow_device = None
try:
    if _use_flow_pv:
        try:
            from hardware import flowmeter as _fmhw
            _flow_device = getattr(_fmhw, 'device', None)
        except Exception:
            # if the hardware.flowmeter module raised (for example, requested serial but failed),
            # leave _flow_device as None and let control loop fallback; a UI component may surface the error.
            logging.exception('Flowmeter import failed while configuring blower flow PV')
            _flow_device = None
except Exception:
    _flow_device = None


def set_setpoint(value: float):
    global _blower_setpoint
    with _blower_setpoint_lock:
        _blower_setpoint = float(value)
        _pid.setpoint = _blower_setpoint


def get_setpoint() -> float:
    with _blower_setpoint_lock:
        return float(_blower_setpoint)


def get_parameter() -> float:
    return float(blowerdac_device.get_parameter())

def _control_loop():
    while True:
        try:
            if _is_dummy_blower:
                # Disable PID for dummy blower - use setpoint directly
                with _blower_setpoint_lock:
                    voltage = _blower_setpoint
                blowerdac_device.set_voltage(voltage)
            else:
                # Real blower: use PID control
                # choose process variable: flowmeter reading if enabled and available, else blower parameter
                if _use_flow_pv and (_flow_device is not None):
                    try:
                        pv = float(_flow_device.get_flow())
                    except Exception:
                        logging.exception('Failed to read flowmeter for control PV; falling back to blower parameter')
                        pv = float(blowerdac_device.get_parameter())
                else:
                    pv = float(blowerdac_device.get_parameter())
                pid_out = _pid(pv)
                print('dac voltage set to:', pid_out)
                blowerdac_device.set_voltage(pid_out)
        except Exception:
            logging.exception('Blower control loop error')
        time.sleep(UPDATE_INTERVAL)
def start_blower():
    global _blower_thread, _blower_sim_thread
    if globals().get('_blower_thread') is not None and _blower_thread.is_alive():
        return
    _blower_thread = threading.Thread(target=_control_loop, daemon=True)
    _blower_thread.start()
    # do not start a simulator thread (simulation removed)
